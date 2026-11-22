import hashlib
import hmac
import unittest

from supportbot.meta import extract_messages, verify_signature


class MetaWebhookTests(unittest.TestCase):
    def test_signature_is_checked(self):
        body = b'{"object":"page"}'
        secret = 'test-secret'
        signature = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(body, signature, secret))
        self.assertFalse(verify_signature(body, 'sha256=invalid', secret))
        self.assertFalse(verify_signature(body, signature, ''))

    def test_extracts_whatsapp_text_without_logging_or_transforming_it(self):
        payload = {
            'object': 'whatsapp_business_account',
            'entry': [{'changes': [{'value': {'messages': [
                {'from': '15550001111', 'text': {'body': 'Where can I get support?'}},
            ]}}]}],
        }
        self.assertEqual(
            extract_messages(payload),
            [('whatsapp', '15550001111', 'Where can I get support?')],
        )

    def test_skips_messenger_echoes(self):
        payload = {
            'object': 'page',
            'entry': [{'messaging': [
                {'sender': {'id': 'user-1'}, 'message': {'text': 'Help'}},
                {'sender': {'id': 'page-1'}, 'message': {'text': 'Echo', 'is_echo': True}},
            ]}],
        }
        self.assertEqual(extract_messages(payload), [('messenger', 'user-1', 'Help')])


if __name__ == '__main__':
    unittest.main()
