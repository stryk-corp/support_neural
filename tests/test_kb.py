import tempfile
import unittest
from pathlib import Path

from supportbot.kb import SQLiteKBStore, build_public_documents, refresh_from_files


class PublicKnowledgeTests(unittest.TestCase):
    def test_only_approved_safe_paths_are_indexed(self):
        kb = {
            'company_identity': {'brand_name': 'NeuralVillage', 'founding_year': '2020'},
            'repository_verified_facts': {'secret_note': 'Do not expose this.'},
            'kb_meta': {'fields_needing_verification': ['Unverified claim']},
        }
        docs = build_public_documents(kb, [
            'company_identity.brand_name',
            'company_identity.founding_year',
            'repository_verified_facts.secret_note',
            'kb_meta.fields_needing_verification',
        ])
        self.assertEqual([doc['content'] for doc in docs], ['NeuralVillage', '2020'])

    def test_secret_like_values_are_never_indexed(self):
        kb = {'company_identity': {'tagline': 'api_key=private-value-123456'}}
        docs = build_public_documents(kb, ['company_identity.tagline'])
        self.assertEqual(docs, [])

    def test_workflow_library_is_loaded_as_neuralspace_documents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SQLiteKBStore(root / 'index.sqlite3')
            library = root / 'workflows.json'
            library.write_text(
                '[{"id":"brainlab-csv","scope":"public","title":"Brain Lab CSV","content":"Upload a CSV under 2 MB."}]',
                encoding='utf-8',
            )
            from supportbot.kb import refresh_workflow_library
            result = refresh_workflow_library(store, library)
            rows = store.search('Brain Lab CSV 2 MB')
            self.assertEqual(result['documents'], 1)
            self.assertTrue(rows)
            self.assertEqual(rows[0]['path'], 'customer_support_navigation.brainlab-csv')

    def test_refresh_builds_searchable_local_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kb_path = root / 'kb.json'
            paths_path = root / 'paths.json'
            kb_path.write_text(
                '{"company_identity":{"brand_name":"NeuralVillage"}}',
                encoding='utf-8',
            )
            paths_path.write_text('["company_identity.brand_name"]', encoding='utf-8')
            store = SQLiteKBStore(root / 'index.sqlite3')
            result = refresh_from_files(store, kb_path, paths_path)
            self.assertEqual(result['documents'], 1)
            self.assertEqual(store.search('NeuralVillage')[0]['content'], 'NeuralVillage')

    def test_approved_array_item_path_can_be_indexed(self):
        kb = {'faq': [{'answer': 'Safe answer.'}]}
        docs = build_public_documents(kb, ['faq[0].answer'])
        self.assertEqual(docs[0]['content'], 'Safe answer.')

    def test_scoped_search_includes_public_but_never_neighbor_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteKBStore(Path(temp_dir) / 'scopes.sqlite3')
            store.replace_documents([{
                'doc_id': 'public', 'path': 'public', 'content': 'Company hours are weekdays.'
            }], tenant_id='org-a', scope_id='public')
            store.replace_documents([{
                'doc_id': 'group-a', 'path': 'group-a', 'content': 'Group alpha meets in room cedar.'
            }], tenant_id='org-a', scope_id='whatsapp:alpha')
            store.replace_documents([{
                'doc_id': 'group-b', 'path': 'group-b', 'content': 'Group beta meets in room maple.'
            }], tenant_id='org-a', scope_id='whatsapp:beta')
            store.replace_documents([{
                'doc_id': 'other-tenant', 'path': 'other-tenant', 'content': 'Other tenant room birch.'
            }], tenant_id='org-b', scope_id='whatsapp:alpha')

            alpha = store.search('cedar', tenant_id='org-a', scope_id='whatsapp:alpha')
            beta = store.search('cedar', tenant_id='org-a', scope_id='whatsapp:beta')
            other_tenant = store.search('birch', tenant_id='org-a', scope_id='whatsapp:alpha')
            self.assertEqual([doc['path'] for doc in alpha], ['group-a'])
            self.assertEqual(beta, [])
            self.assertEqual(other_tenant, [])

    def test_feedback_requires_review_before_scope_knowledge_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteKBStore(Path(temp_dir) / 'feedback.sqlite3')
            feedback_id = store.add_feedback(
                'org-a', 'whatsapp:alpha', ['faq[15].answer'], -1,
                'The approved support hours are weekdays.',
            )
            self.assertEqual(len(store.list_feedback('org-a', 'whatsapp:alpha')), 1)
            self.assertTrue(store.approve_feedback(feedback_id, 'org-a', 'whatsapp:alpha'))
            self.assertEqual(
                store.search('support hours', tenant_id='org-a', scope_id='whatsapp:alpha')[0]['path'],
                'reviewed_feedback',
            )
            self.assertEqual(
                store.search('support hours', tenant_id='org-a', scope_id='whatsapp:beta'),
                [],
            )


if __name__ == '__main__':
    unittest.main()
