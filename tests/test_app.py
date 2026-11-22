import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from wsgiref.util import setup_testing_defaults

from supportbot.app import SupportBotApplication
from supportbot.kb import SQLiteKBStore


class FakeGenerator:
    def __init__(self):
        self.calls = 0
        self.histories = []

    def generate(self, question, documents, history=None):
        self.calls += 1
        self.histories.append(history or [])
        return 'The support contact is help@example.test.'


class SupportBotHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = SQLiteKBStore(root / 'support.sqlite3')
        self.store.replace_documents([{
            'doc_id': 'contact-1',
            'path': 'contact_and_support.general_email',
            'content': 'The general support contact is help@example.test.',
        }])
        self.generator = FakeGenerator()
        self.settings = SimpleNamespace(
            database_path=root / 'support.sqlite3',
            kb_path=root / 'absent-kb.json',
            approved_paths_path=root / 'absent-paths.json',
            gemini_api_key='test-key',
            gemini_model='gemini-test',
            gemini_daily_call_limit=5,
            cache_ttl_seconds=3600,
            max_requests_per_minute=30,
            max_input_chars=2000,
            allowed_origins=frozenset({'https://www.example.test'}),
            admin_token='admin-test-token',
            meta_app_secret='',
            meta_verify_token='',
            meta_access_token='',
            meta_graph_version='v24.0',
            whatsapp_phone_number_id='',
            messenger_page_id='',
            instagram_user_id='',
        )
        self.app = SupportBotApplication(
            self.settings,
            self.store,
            self.generator,
            meta_client=object(),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, path, method='GET', payload=None, origin='', authorization=''):
        body = json.dumps(payload).encode('utf-8') if payload is not None else b''
        environ = {}
        setup_testing_defaults(environ)
        environ.update({
            'PATH_INFO': path,
            'REQUEST_METHOD': method,
            'CONTENT_LENGTH': str(len(body)),
            'wsgi.input': io.BytesIO(body),
            'REMOTE_ADDR': '127.0.0.1',
            'HTTP_ORIGIN': origin,
            'HTTP_AUTHORIZATION': authorization,
        })
        captured = {}

        def start_response(status, headers):
            captured['status'] = status
            captured['headers'] = dict(headers)

        result = b''.join(self.app(environ, start_response))
        return captured, result

    def test_allowed_origin_can_chat(self):
        response, body = self.request(
            '/api/chat',
            method='POST',
            payload={'message': 'What is the support contact?'},
            origin='https://www.example.test',
        )
        self.assertEqual(response['status'], '200 OK')
        self.assertEqual(response['headers']['Access-Control-Allow-Origin'], 'https://www.example.test')
        self.assertIn(b'answer', body)

    def test_unapproved_origin_is_rejected(self):
        response, _ = self.request(
            '/api/chat',
            method='POST',
            payload={'message': 'What is the support contact?'},
            origin='https://attacker.example',
        )
        self.assertEqual(response['status'], '403 Forbidden')
        self.assertEqual(self.generator.calls, 0)

    def test_admin_status_requires_bearer_token(self):
        denied, _ = self.request('/admin/status')
        allowed, body = self.request(
            '/admin/status',
            authorization='Bearer admin-test-token',
        )
        self.assertEqual(denied['status'], '404 Not Found')
        self.assertEqual(allowed['status'], '200 OK')
        self.assertIn(b'documents', body)

    def test_demo_page_is_served_from_same_origin(self):
        response, body = self.request('/demo')
        self.assertEqual(response['status'], '200 OK')
        self.assertEqual(response['headers']['Content-Type'], 'text/html; charset=utf-8')
        self.assertIn(b'Talk with Ladi', body)
        self.assertIn(b'/api/chat', body)

    def test_health_reports_gemini_readiness_as_boolean(self):
        response, body = self.request('/health')
        self.assertEqual(response['status'], '200 OK')
        self.assertEqual(json.loads(body), {'ok': True, 'gemini_configured': True})

    def test_web_scope_defaults_to_public_for_unmapped_origin(self):
        tenant_id, scope_id = self.app._web_scope('https://www.example.test')
        self.assertEqual((tenant_id, scope_id), ('neuralvillage', 'public'))

    def test_feedback_is_rejected_for_personal_medical_or_secret_text(self):
        response, _ = self.request(
            '/api/feedback',
            method='POST',
            payload={'rating': -1, 'source_paths': ['faq[14].answer'], 'correction': 'My password=secret and I have symptoms'},
            origin='https://www.example.test',
        )
        self.assertEqual(response['status'], '400 Bad Request')

    def test_feedback_queue_accepts_general_correction(self):
        response, body = self.request(
            '/api/feedback',
            method='POST',
            payload={'rating': 1, 'source_paths': ['faq[14].answer'], 'correction': 'NeuralVillage is a social-impact brain-health venture.'},
            origin='https://www.example.test',
        )
        self.assertEqual(response['status'], '202 Accepted')
        self.assertIn(b'feedback_id', body)

    def test_chat_api_forwards_validated_history(self):
        response, _ = self.request(
            '/api/chat',
            method='POST',
            payload={
                'message': 'Can you clarify?',
                'history': [{'role': 'user', 'content': 'What is the support contact?'}],
            },
            origin='https://www.example.test',
        )
        self.assertEqual(response['status'], '200 OK')
        self.assertEqual(self.generator.histories[0], [
            {'role': 'user', 'content': 'What is the support contact?'}
        ])

    def test_admin_knowledge_accepts_approved_document(self):
        response, body = self.request(
            '/admin/knowledge',
            method='POST',
            payload={
                'tenant_id': 'neuralvillage',
                'scope_id': 'public',
                'documents': [{
                    'path': 'customer_support.general',
                    'content': 'Support is available by email.',
                }],
            },
            authorization='Bearer admin-test-token',
        )
        self.assertEqual(response['status'], '200 OK')
        self.assertIn(b'"indexed": 1', body)

    def test_admin_knowledge_rejects_secret_content(self):
        response, _ = self.request(
            '/admin/knowledge',
            method='POST',
            payload={
                'tenant_id': 'neuralvillage',
                'scope_id': 'public',
                'documents': [{
                    'path': 'customer_support.general',
                    'content': 'api_key=sample-secret',
                }],
            },
            authorization='Bearer admin-test-token',
        )
        self.assertEqual(response['status'], '400 Bad Request')

    def test_admin_reindex_reports_missing_source(self):
        response, body = self.request(
            '/admin/reindex',
            method='POST',
            authorization='Bearer admin-test-token',
        )
        self.assertEqual(response['status'], '400 Bad Request')
        self.assertIn(b'KB refresh failed', body)

    def test_admin_feedback_queue_lists_submitted_correction(self):
        self.request(
            '/api/feedback',
            method='POST',
            payload={
                'rating': -1,
                'source_paths': ['faq[14].answer'],
                'correction': 'Update the listed support hours.',
            },
            origin='https://www.example.test',
        )
        response, body = self.request(
            '/admin/feedback',
            authorization='Bearer admin-test-token',
        )
        self.assertEqual(response['status'], '200 OK')
        self.assertIn(b'items', body)

    def test_meta_webhook_rejects_invalid_signature(self):
        response, _ = self.request(
            '/webhooks/meta',
            method='POST',
            payload={'entry': []},
        )
        self.assertEqual(response['status'], '403 Forbidden')

    def test_options_request_allows_configured_origin(self):
        response, body = self.request(
            '/api/chat',
            method='OPTIONS',
            origin='https://www.example.test',
        )
        self.assertEqual(response['status'], '204 No Content')
        self.assertEqual(body, b'')

    def test_unknown_route_returns_not_found(self):
        response, _ = self.request('/does-not-exist')
        self.assertEqual(response['status'], '404 Not Found')

    def test_invalid_chat_payload_is_rejected(self):
        response, _ = self.request(
            '/api/chat',
            method='POST',
            payload={'message': 123},
            origin='https://www.example.test',
        )
        self.assertEqual(response['status'], '400 Bad Request')

    def test_meta_verification_rejects_wrong_token(self):
        response, _ = self.request(
            '/webhooks/meta',
            payload={'hub.mode': 'subscribe'},
        )
        self.assertEqual(response['status'], '403 Forbidden')


if __name__ == '__main__':
    unittest.main()