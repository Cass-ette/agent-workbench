import importlib.util
import stat
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 and older, without an extra dependency.
    tomllib = None


BACKEND_PATH = Path(__file__).resolve().parents[1] / "backend" / "workbench_backend.py"
SPEC = importlib.util.spec_from_file_location("workbench_backend", BACKEND_PATH)
BACKEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKEND)


class CodexConfigTests(unittest.TestCase):
    def test_claude_keeps_network_proxy_without_inheriting_other_model_auth(self):
        with patch.dict(BACKEND.os.environ, {
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "no_proxy": ".internal.test",
            "NO_PROXY": ".internal.test,build.test",
            "CLAUDE_CODE_OAUTH_TOKEN": "external-login",
        }):
            _, environment = BACKEND.build_launch("private-key", "test", workspace=Path("/tmp/project"))
            self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7890")
            self.assertEqual(environment["NO_PROXY"], "127.0.0.1,localhost,::1,.internal.test,build.test")
            self.assertEqual(environment["NO_PROXY"], environment["no_proxy"])
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", environment)

    def test_permission_modes_cover_new_resume_and_fork_without_disabling_approvals(self):
        for mode in ("restricted", "standard", "full"):
            for session_mode, history_id in (("new", None), ("continue", None), ("resume", None), ("resume", "test-history")):
                with self.subTest(mode=mode, session_mode=session_mode, history_id=history_id):
                    args = BACKEND.build_codex_command("test", Path("/tmp/project"), session_mode, history_id,
                                                       permission_mode=mode)
                    self.assertEqual(args[args.index("--ask-for-approval") + 1], "on-request")
                    self.assertEqual(args[args.index("--sandbox") + 1], "danger-full-access" if mode == "full" else "workspace-write")
                    self.assertIn("sandbox_workspace_write.network_access=" + ("false" if mode == "restricted" else "true"), args)
                    self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        with self.assertRaises(RuntimeError):
            BACKEND.agent_permission_args("codex", "invalid")

    def test_claude_requires_explicit_full_mode_to_skip_tool_confirmation(self):
        for mode in ("restricted", "standard", "full"):
            command, _ = BACKEND.build_launch("test", "test", workspace=Path("/tmp/project"), permission_mode=mode)
            self.assertEqual(command[command.index("--permission-mode") + 1], "bypassPermissions" if mode == "full" else "default")
        command, _ = BACKEND.build_launch("test", "test", smoke=True, permission_mode="full")
        self.assertNotIn("bypassPermissions", command)

    def test_session_modes_only_resume_when_requested(self):
        BACKEND.CODEX = Path("/fake/codex")
        workspace = Path("/tmp/project")

        new_command = BACKEND.build_codex_command("model-a", workspace, "new")
        continue_command = BACKEND.build_codex_command("model-a", workspace, "continue")
        resume_command = BACKEND.build_codex_command("model-a", workspace, "resume")

        self.assertNotIn("resume", new_command)
        self.assertEqual(continue_command[1:3], ["resume", "--last"])
        self.assertEqual(resume_command[1], "resume")
        with self.assertRaises(RuntimeError):
            BACKEND.build_codex_command("model-a", workspace, "unknown")

    def test_codex_environment_uses_workbench_key_and_drops_stale_names(self):
        with patch.dict(BACKEND.os.environ, {
            "CCH_API_KEY": "stale-legacy-key", "WORKBENCH_GATEWAY_KEY": "stale-workbench-key",
            "OPENAI_API_KEY": "external", "OPENAI_BASE_URL": "https://external.test", "CODEX_HOME": "/external/home",
        }):
            environment = BACKEND.codex_environment(Path("/isolated/home"), "fresh-key")
        self.assertEqual(environment["WORKBENCH_GATEWAY_KEY"], "fresh-key")
        self.assertEqual(environment["CODEX_HOME"], "/isolated/home")
        for name in ("CCH_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_SQLITE_HOME"):
            self.assertNotIn(name, environment)

    def test_legacy_layout_moves_once_and_carries_the_backup_pointer(self):
        with tempfile.TemporaryDirectory() as home:
            legacy = Path(home) / ".config/cchp-client/claude-test"
            legacy.mkdir(parents=True)
            (legacy / "key-profiles.json").write_text("[]")
            (legacy / "keys").mkdir()
            (legacy / "keys/secret").write_text("x" * 30)
            (legacy / "keys/secret").chmod(0o600)
            (Path(home) / ".config/cchp-client/latest-backup.txt").write_text("/somewhere/backup\n")
            with patch.object(BACKEND, "LEGACY_TEST_ROOT", legacy), \
                 patch.object(BACKEND, "LEGACY_CLIENT_ROOT", legacy.parent), \
                 patch.object(BACKEND, "TEST_ROOT", Path(home) / ".config/agent-workbench"):
                BACKEND.migrate_legacy_layout()
                target = Path(home) / ".config/agent-workbench"
                self.assertEqual((target / "key-profiles.json").read_text(), "[]")
                self.assertEqual((target / "keys/secret").stat().st_mode & 0o777, 0o600)
                self.assertEqual((target / "latest-backup.txt").read_text().strip(), "/somewhere/backup")
                self.assertFalse(legacy.parent.exists())
                # Second run is a no-op.
                BACKEND.migrate_legacy_layout()
                self.assertTrue((target / "keys/secret").is_file())

    def test_legacy_layout_never_overwrites_an_existing_new_layout(self):
        with tempfile.TemporaryDirectory() as home:
            legacy = Path(home) / ".config/cchp-client/claude-test"
            legacy.mkdir(parents=True)
            (legacy / "key-profiles.json").write_text('[{"legacy": true}]')
            target = Path(home) / ".config/agent-workbench"
            target.mkdir(parents=True)
            (target / "key-profiles.json").write_text('[{"new": true}]')
            with patch.object(BACKEND, "LEGACY_TEST_ROOT", legacy), \
                 patch.object(BACKEND, "LEGACY_CLIENT_ROOT", legacy.parent), \
                 patch.object(BACKEND, "TEST_ROOT", target):
                BACKEND.migrate_legacy_layout()
            self.assertEqual((target / "key-profiles.json").read_text(), '[{"new": true}]')
            self.assertTrue(legacy.is_dir())

    def test_legacy_layout_keeps_a_non_empty_legacy_parent(self):
        with tempfile.TemporaryDirectory() as home:
            legacy = Path(home) / ".config/cchp-client/claude-test"
            legacy.mkdir(parents=True)
            (legacy / "key-profiles.json").write_text("[]")
            (legacy.parent / "unrelated").write_text("keep me")
            target = Path(home) / ".config/agent-workbench"
            with patch.object(BACKEND, "LEGACY_TEST_ROOT", legacy), \
                 patch.object(BACKEND, "LEGACY_CLIENT_ROOT", legacy.parent), \
                 patch.object(BACKEND, "TEST_ROOT", target):
                BACKEND.migrate_legacy_layout()
            self.assertFalse(legacy.exists())
            self.assertEqual((legacy.parent / "unrelated").read_text(), "keep me")

    def test_legacy_layout_is_a_no_op_without_the_old_directory(self):
        with tempfile.TemporaryDirectory() as home:
            target = Path(home) / ".config/agent-workbench"
            with patch.object(BACKEND, "LEGACY_TEST_ROOT", Path(home) / ".config/cchp-client/claude-test"), \
                 patch.object(BACKEND, "LEGACY_CLIENT_ROOT", Path(home) / ".config/cchp-client"), \
                 patch.object(BACKEND, "TEST_ROOT", target):
                BACKEND.migrate_legacy_layout()
            self.assertFalse(target.exists())
            self.assertFalse((Path(home) / ".config").exists())

    def test_history_launch_uses_native_fork(self):
        BACKEND.CODEX = Path("/fake/codex")
        command = BACKEND.build_codex_command(
            "model-a", Path("/tmp/project"), "resume", "01a1ec86-bf14-70f2-8144-874b179beb72")
        self.assertEqual(command[:3], ["/fake/codex", "fork", "01a1ec86-bf14-70f2-8144-874b179beb72"])
        self.assertNotIn("resume", command)

    def test_matching_history_resumes_in_place(self):
        BACKEND.CODEX = Path("/fake/codex")
        session_id = "01a1ec86-bf14-70f2-8144-874b179beb72"
        command = BACKEND.build_codex_command(
            "model-a", Path("/tmp/project"), "resume", session_id, fork_history=False)
        self.assertEqual(command[:3], ["/fake/codex", "resume", session_id])
        self.assertNotIn("fork", command)

    def test_creates_private_responses_provider_per_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            BACKEND.CODEX_HOME_ROOT = Path(temporary)

            first_home = BACKEND.codex_config("p-test-one", "glm-5.3", "http://127.0.0.1:8091")
            second_home = BACKEND.codex_config("p-test-two", "kimi-for-coding-highspeed", "http://127.0.0.1:8092")

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
                self.assertEqual(first["model_provider"], "workbench_gateway")
                self.assertEqual(first["model_providers"]["workbench_gateway"]["base_url"], "http://127.0.0.1:8091/v1")
                self.assertEqual(first["model_providers"]["workbench_gateway"]["wire_api"], "responses")
                self.assertEqual(first["model_providers"]["workbench_gateway"]["env_key"], "WORKBENCH_GATEWAY_KEY")
                self.assertEqual(second["model"], "kimi-for-coding-highspeed")
            else:
                self.assertIn('model = "glm-5.3"', first_text)
                self.assertIn('model_provider = "workbench_gateway"', first_text)
                self.assertIn('base_url = "http://127.0.0.1:8091/v1"', first_text)
                self.assertIn('wire_api = "responses"', first_text)
                self.assertIn('env_key = "WORKBENCH_GATEWAY_KEY"', first_text)
                self.assertIn('model = "kimi-for-coding-highspeed"', second_text)
            self.assertNotEqual(first_home, second_home)
            self.assertEqual(stat.S_IMODE(first_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
