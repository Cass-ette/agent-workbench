import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import agent_reasoning as reasoning

spec = importlib.util.spec_from_file_location("reasoning_backend", Path(__file__).resolve().parents[1] / "backend/workbench_backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class ReasoningTests(unittest.TestCase):
    def test_default_omits_override_and_explicit_levels_preserve_model_selection(self):
        self.assertEqual(reasoning.arguments("codex", None), [])
        self.assertEqual(reasoning.arguments("claude", "default"), [])
        self.assertEqual(reasoning.config_line(None, 'model_reasoning_effort = "ultra"\n'), 'model_reasoning_effort = "ultra"\n')
        for level in reasoning.LEVELS:
            self.assertEqual(reasoning.arguments("codex", level), ["-c", 'model_reasoning_effort="' + level + '"'])
            self.assertEqual(reasoning.arguments("claude", level), ["--effort", level])
        for value in ("none", "high\n/model other", "high;whoami", 1, {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reasoning.normalize(value)

    def test_effort_does_not_change_home_identity_and_default_preserves_native_setting(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(backend, "CODEX_HOME_ROOT", Path(temporary)):
            home = backend.codex_config("profile", "glm-5.3", "https://gateway.test/api/v1", Path("/project"))
            self.assertNotIn("model_reasoning_effort", (home / "config.toml").read_text())
            override = backend.codex_config("profile", "glm-5.3", "https://gateway.test/api/v1", Path("/project"), reasoning_effort="high")
            self.assertEqual(home, override)
            self.assertIn('model_reasoning_effort = "high"', (home / "config.toml").read_text())
            backend.codex_config("profile", "glm-5.3", "https://gateway.test/api/v1", Path("/project"))
            self.assertIn('model_reasoning_effort = "high"', (home / "config.toml").read_text())
            command = backend.build_codex_command("glm-5.3", Path("/project"), "resume", "session-id", fork_history=False, reasoning_effort="low")
            self.assertEqual(command[1:3], ["resume", "session-id"])
            self.assertEqual(command[command.index("--model") + 1], "glm-5.3")
            self.assertIn('model_reasoning_effort="low"', command)

    def test_claude_effort_is_not_overridden_by_inherited_disable_or_env_level(self):
        with patch.dict(os.environ, {"MAX_THINKING_TOKENS":"0", "CLAUDE_CODE_EFFORT_LEVEL":"low"}):
            command, env = backend.build_launch("dummy", "gateway-model", smoke=True, reasoning_effort="high")
        self.assertNotIn("MAX_THINKING_TOKENS", env)
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", env)
        self.assertEqual(command[command.index("--effort") + 1], "high")
        self.assertEqual(command[command.index("--model") + 1], "gateway-model")
