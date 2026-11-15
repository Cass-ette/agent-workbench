import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 and older, without an extra dependency.
    tomllib = None


BACKEND_PATH = Path(__file__).resolve().parents[1] / "backend" / "claude-cchp-test.py"
SPEC = importlib.util.spec_from_file_location("claude_cchp_backend", BACKEND_PATH)
BACKEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKEND)


class CodexConfigTests(unittest.TestCase):
    def test_creates_private_responses_provider_per_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            BACKEND.CODEX_HOME_ROOT = Path(temporary)

            first_home = BACKEND.codex_config("p-test-one", "glm-5.3")
            second_home = BACKEND.codex_config("p-test-two", "kimi-for-coding-highspeed")

            first_path = first_home / "config.toml"
            first_text = first_path.read_text()
            second_text = (second_home / "config.toml").read_text()
            if tomllib is not None:
                first = tomllib.loads(first_text)
                second = tomllib.loads(second_text)
            else:
                first = second = None

            if first is not None:
                self.assertEqual(first["model"], "glm-5.3")
                self.assertEqual(first["model_provider"], "cchp_local")
                self.assertEqual(first["model_providers"]["cchp_local"]["base_url"], "http://127.0.0.1:8081/v1")
                self.assertEqual(first["model_providers"]["cchp_local"]["wire_api"], "responses")
                self.assertEqual(first["model_providers"]["cchp_local"]["env_key"], "CCH_API_KEY")
                self.assertEqual(second["model"], "kimi-for-coding-highspeed")
            else:
                self.assertIn('model = "glm-5.3"', first_text)
                self.assertIn('model_provider = "cchp_local"', first_text)
                self.assertIn('base_url = "http://127.0.0.1:8081/v1"', first_text)
                self.assertIn('wire_api = "responses"', first_text)
                self.assertIn('env_key = "CCH_API_KEY"', first_text)
                self.assertIn('model = "kimi-for-coding-highspeed"', second_text)
            self.assertNotEqual(first_home, second_home)
            self.assertEqual(stat.S_IMODE(first_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
