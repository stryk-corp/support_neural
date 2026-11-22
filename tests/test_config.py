import tempfile
import unittest
from pathlib import Path

from supportbot.config import load_env_files


class EnvFileTests(unittest.TestCase):
    def test_private_dotenv_precedence_and_allowlist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / 'supportbot'
            package.mkdir()
            (root / '.env').write_text(
                'GEMINI_API_KEY=root-key\nUNRELATED_SECRET=do-not-load\n',
                encoding='utf-8',
            )
            (package / '.env').write_text('GEMINI_API_KEY=private-key\n', encoding='utf-8')
            (package / '.env.example').write_text('GEMINI_API_KEY=sample-key\n', encoding='utf-8')
            env = {}
            load_env_files(env, root, package)
            self.assertEqual(env['GEMINI_API_KEY'], 'private-key')
            self.assertNotIn('UNRELATED_SECRET', env)

    def test_process_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / 'supportbot'
            package.mkdir()
            (package / '.env').write_text('GEMINI_API_KEY=file-key\n', encoding='utf-8')
            env = {'GEMINI_API_KEY': 'process-key'}
            load_env_files(env, root, package)
            self.assertEqual(env['GEMINI_API_KEY'], 'process-key')


if __name__ == '__main__':
    unittest.main()