import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from supportbot.kb import SQLiteKBStore
from supportbot.service import ChatService, FALLBACK


class FakeGenerator:
    def __init__(self):
        self.calls = 0
        self.histories = []
        self.questions = []
        self.documents = []

    def generate(self, question, documents, history=None):
        self.calls += 1
        self.histories.append(history or [])
        self.questions.append(question)
        self.documents.append(documents)
        return 'The approved support email is help@example.test.'


class ChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteKBStore(Path(self.temp_dir.name) / 'index.sqlite3')
        self.store.replace_documents([{
            'doc_id': 'doc-1',
            'path': 'contact_and_support.general_email',
            'content': 'The general support email is help@example.test.',
        }])
        self.settings = SimpleNamespace(
            max_input_chars=2000,
            gemini_api_key='test-key',
            gemini_daily_call_limit=5,
            cache_ttl_seconds=3600,
        )
        self.generator = FakeGenerator()
        self.service = ChatService(self.store, self.settings, self.generator)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cache_hit_does_not_spend_another_model_call(self):
        first = self.service.answer('What email is support?')
        second = self.service.answer('What email is support?')
        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertEqual(self.generator.calls, 1)

    def test_personal_question_is_not_cached(self):
        question = 'Can you confirm alice@example.test is the right email?'
        self.service.answer(question)
        self.service.answer(question)
        self.assertEqual(self.generator.calls, 2)

    def test_medical_advice_is_blocked_without_model_call(self):
        result = self.service.answer('Can you diagnose my symptoms?')
        self.assertEqual(result['answer'], FALLBACK)
        self.assertEqual(self.generator.calls, 0)

    def test_unmatched_question_still_converses_and_calls_model(self):
        result = self.service.answer('Tell me about quantum cooking.')
        self.assertEqual(result['answer'], 'The approved support email is help@example.test.')
        self.assertEqual(self.generator.calls, 1)

    def test_greeting_without_kb_facts_calls_model(self):
        result = self.service.answer('Hi')
        self.assertEqual(result['answer'], 'The approved support email is help@example.test.')
        self.assertEqual(self.generator.calls, 1)

    def test_greeting_uses_ladi_persona_during_provider_quota(self):
        class QuotaError(RuntimeError):
            status = 429
        def raise_quota(question, documents, history=None):
            raise QuotaError()
        self.generator.generate = raise_quota
        result = self.service.answer('Hi')
        self.assertIn("I'm Ladi", result['answer'])
        self.assertEqual(result['provider_status'], 'quota_limited')

    def test_navigation_answer_never_calls_gemini(self):
        class UnexpectedGenerator:
            calls = 0
            def generate(self, *args, **kwargs):
                self.calls += 1
                raise AssertionError('Navigation questions must not call Gemini')
        self.generator = UnexpectedGenerator()
        self.service.generator = self.generator
        result = self.service.answer('How do I navigate the Benchmark Brain Lab?')
        self.assertIn('/benchmark/', result['answer'])
        self.assertEqual(self.generator.calls, 0)

    def test_measurement_navigation_takes_brainlab_priority(self):
        result = self.service.answer('How can I take a measurement in NeuralSpace Brain Lab?')
        self.assertIn('/benchmark/', result['answer'])
        self.assertIn('recorded_at', result['answer'])
        self.assertIn('2 MB', result['answer'])

    def test_navigation_fallback_works_during_provider_quota(self):
        class QuotaError(RuntimeError):
            status = 429
        def raise_quota(question, documents, history=None):
            raise QuotaError()
        self.generator.generate = raise_quota
        result = self.service.answer('How do I navigate the Benchmark Brain Lab?')
        self.assertIn('CSV', result['answer'])
        self.assertIn('/benchmark/', result['answer'])
        self.assertNotIn('provider_status', result)

    def test_provider_error_is_safe_and_statused(self):
        class QuotaError(RuntimeError):
            status = 429
        def raise_quota(question, documents, history=None):
            raise QuotaError()
        self.generator.generate = raise_quota
        result = self.service.answer('What is NeuralVillage?')
        self.assertEqual(result['provider_status'], 'quota_limited')
        self.assertIn('quota', result['answer'])

    def test_daily_model_call_limit_stops_generation(self):
        self.settings.gemini_daily_call_limit = 0
        result = self.service.answer('What email is support?')
        self.assertEqual(result['answer'], FALLBACK)
        self.assertEqual(self.generator.calls, 0)

    def test_about_faqs_are_gemini_generated_not_raw_local_answers(self):
        self.store.replace_documents([
            {
                'doc_id': 'faq-village',
                'path': 'faq[14].answer',
                'content': 'NeuralVillage is a social-impact venture.',
            },
            {
                'doc_id': 'faq-space',
                'path': 'faq[15].answer',
                'content': 'NeuralSpace is NeuralVillage\'s brain-health platform.',
            },
        ])
        village = self.service.answer('What is NeuralVillage?')
        space = self.service.answer('What is NeuralSpace?')
        space_variant = self.service.answer('Neural space?')
        self.assertEqual(village['answer'], 'The approved support email is help@example.test.')
        self.assertEqual(space['answer'], 'The approved support email is help@example.test.')
        self.assertEqual(space_variant['answer'], 'The approved support email is help@example.test.')
        self.assertEqual(self.generator.calls, 3)
        self.assertTrue(any(doc['path'] == 'faq[14].answer' for doc in self.generator.documents[0]))
        self.assertTrue(any(doc['path'] == 'faq[15].answer' for doc in self.generator.documents[1]))

    def test_faq_without_gemini_does_not_return_raw_answer(self):
        self.settings.gemini_api_key = ''
        self.store.replace_documents([{
            'doc_id': 'faq-village',
            'path': 'faq[14].answer',
            'content': 'NeuralVillage is a social-impact venture.',
        }])
        result = self.service.answer('What is NeuralVillage?')
        self.assertEqual(result['answer'], FALLBACK)
        self.assertEqual(self.generator.calls, 0)

    def test_small_talk_uses_only_ladi_persona_fact(self):
        result = self.service.answer('How are you doing?')
        self.assertEqual(result['answer'], 'The approved support email is help@example.test.')
        self.assertEqual(self.generator.calls, 1)
        self.assertEqual([doc['path'] for doc in self.generator.documents[0]], ['ladi.persona'])

    def test_followup_context_retrieves_prior_topic_without_persisting(self):
        history = [{'role': 'user', 'content': 'What email can I use for support?'}]
        result = self.service.answer('Can you clarify?', history=history)
        self.assertIn('support email', result['answer'])
        self.assertEqual(self.generator.calls, 1)
        self.assertEqual(self.generator.histories[0], history)
        cached = self.store.get_cached(
            self.service._cache_key('Can you clarify?', self.store.search('support email')),
            9999999999,
        )
        self.assertIsNone(cached)

    def test_sensitive_history_is_not_forwarded_to_gemini(self):
        result = self.service.answer(
            'What email can I use?',
            history=[{'role': 'user', 'content': 'My symptoms are severe'}],
        )
        self.assertEqual(self.generator.calls, 1)
        self.assertEqual(self.generator.histories[0], [])
        self.assertEqual(result['answer'], 'The approved support email is help@example.test.')


if __name__ == '__main__':
    unittest.main()
