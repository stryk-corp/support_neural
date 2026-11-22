import unittest

from supportbot.refresh import main


class RefreshCommandTests(unittest.TestCase):
    def test_refresh_reports_missing_default_knowledge_source(self):
        with self.assertRaises(ValueError):
            main()


if __name__ == '__main__':
    unittest.main()
