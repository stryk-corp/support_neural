import json
import unittest
from unittest.mock import patch

from supportbot.gemini import GeminiClient


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.value).encode('utf-8')


class GeminiHistoryTests(unittest.TestCase):
    @patch('supportbot.gemini.urllib.request.urlopen')
    def test_recent_context_is_included_in_ephemeral_request(self, urlopen):
        urlopen.return_value = FakeResponse({
            'candidates': [{'content': {'parts': [{'text': 'Here is the follow-up.'}]}}],
        })
        client = GeminiClient('test-key')
        answer = client.generate(
            'Can you explain more?',
            [{'path': 'faq[14].answer', 'content': 'Approved public answer.'}],
            history=[
                {'role': 'user', 'content': 'What is NeuralVillage?'},
                {'role': 'assistant', 'content': 'It is a social-impact venture.'},
            ],
        )
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode('utf-8'))
        prompt = payload['contents'][0]['parts'][0]['text']
        self.assertEqual(answer, 'Here is the follow-up.')
        self.assertIn('Recent conversation', prompt)
        self.assertIn('What is NeuralVillage?', prompt)
        self.assertIn('Approved public answer.', prompt)


if __name__ == '__main__':
    unittest.main()