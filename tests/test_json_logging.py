import logging
import unittest

from supportbot.json_logging import JsonFormatter


class JsonLoggingTests(unittest.TestCase):
    def test_formatter_emits_json_message(self):
        record = logging.LogRecord(
            'supportbot',
            logging.WARNING,
            __file__,
            1,
            'knowledge_refresh_failed',
            (),
            None,
        )
        output = JsonFormatter().format(record)
        self.assertIn('"level": "WARNING"', output)
        self.assertIn('"message": "knowledge_refresh_failed"', output)


if __name__ == '__main__':
    unittest.main()
