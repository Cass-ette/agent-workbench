import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("history_launch_backend", ROOT / "backend/claude-cchp-test.py")
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
H = B.history_module()


class HistoryLaunchTests(unittest.TestCase):
    def test_selected_session_uses_original_home_and_only_changes_config_by_forking(self):
        for client in ("codex", "claude"):
            for action in ("original", "fork-same-model", "fork-other-model"):
                with self.subTest(client=client, action=action), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    workspace = root / "project"
                    workspace.mkdir()
                    executable = root / "client"
                    executable.touch()
                    profile = {"id": "p-one", "name": "One", "gateway": "https://gateway.test"}
                    codex_root = root / "codex"
                    claude_root = root / "claude-branches"
                    home = (codex_root / "p-one" if client == "codex" else claude_root) / "branch-original"
                    session_id = "01a1ec86-bf14-70f2-8144-874b179beb72"
                    if client == "codex":
                        source = home / "sessions/2026/11/21" / f"rollout-test-{session_id}.jsonl"
                        rows = [
                            {"type": "session_meta", "payload": {"id": session_id, "cwd": str(workspace)}},
                            {"type": "turn_context", "payload": {"model": "model-a"}},
                            {"type": "response_item", "payload": {"role": "user", "content": "real request"}},
                        ]
                    else:
                        source = home / "projects/project" / f"{session_id}.jsonl"
                        rows = [
                            {"type": "user", "sessionId": session_id, "cwd": str(workspace),
                             "message": {"content": "real request"}},
                            {"type": "assistant", "sessionId": session_id, "message": {"model": "model-a"}},
                        ]
                    source.parent.mkdir(parents=True)
                    source.write_text("\n".join(map(json.dumps, rows)) + "\n")
                    settings = home / "settings.json"
                    settings.write_text('{"theme":"dark"}\n')
                    (home / "workbench-profile.json").write_text(json.dumps({
                        "profile_id": "p-one", "model": "model-a", "gateway": profile["gateway"],
                        "key_fingerprint": H.key_fingerprint("test-key"),
                    }))
                    original = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
                    with patch.multiple(B, CODEX_HOME_ROOT=codex_root, CLAUDE_BRANCH_ROOT=claude_root,
                                        CONFIG_DIR=root / "legacy", CODEX=executable, CLAUDE=executable), \
                            patch.object(B, "load_profiles", return_value=[profile]), \
                            patch.object(B, "read_profile_key", return_value="test-key"), \
                            patch.object(B, "fetch_models", return_value=["model-a", "model-b"]), \
                            patch.object(B.subprocess, "Popen") as popen:
                        popen.return_value.__enter__.return_value.wait.return_value = 0
                        codex, claude = B.history_roots()
                        (entry,) = H.scan_history(codex, claude)
                        if action == "original":
                            with patch.object(B, "read_profile_key", return_value="replacement-key"):
                                with self.assertRaises(RuntimeError):
                                    B.launch_history(entry["history_ref"], session_id, client, "", "", str(workspace), original=True)
                            popen.assert_not_called()
                            self.assertEqual(B.launch_history(entry["history_ref"], session_id, client,
                                "different-selection", "model-b", str(workspace), original=True, permission_mode="standard"), 0)
                        else:
                            self.assertEqual(B.launch_history(entry["history_ref"], session_id, client, "p-one",
                                "model-b" if action == "fork-other-model" else "model-a", str(workspace), force_fork=True, permission_mode="standard"), 0)
                        command = popen.call_args.args[0]
                        env = popen.call_args.kwargs["env"]
                        launched_home = Path(env["CODEX_HOME"] if client == "codex" else env["CLAUDE_CONFIG_DIR"])
                        if client == "codex":
                            self.assertIn("sandbox_workspace_write.network_access=true", command)
                        else:
                            self.assertEqual(command[command.index("--permission-mode") + 1], "default")
                    if action != "original":
                        self.assertNotEqual(launched_home, home)
                        self.assertEqual(len(list(home.parent.glob("branch-*"))), 2)
                        self.assertIn("fork" if client == "codex" else "--fork-session", command)
                    else:
                        self.assertEqual(launched_home, home)
                        self.assertEqual(len(list(home.parent.glob("branch-*"))), 1)
                        self.assertNotIn("fork" if client == "codex" else "--fork-session", command)
                        self.assertIn(session_id, command)
                    self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_regular_claude_launch_records_configuration_without_relabeling_old_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "project"
            workspace.mkdir()
            settings = root / "settings.json"
            settings.write_text('{"theme":"dark"}\n')
            profile = {"id": "p-one", "name": "One", "gateway": "https://gateway.test"}
            with patch.multiple(B, CLAUDE_BRANCH_ROOT=root / "claude-branches", SETTINGS=settings), \
                    patch.object(B, "load_profiles", return_value=[profile]), \
                    patch.object(B, "read_profile_key", return_value="test-key"), \
                    patch.object(B, "fetch_models", return_value=["model-a", "model-b"]), \
                    patch.object(B, "remember_workspace"), patch.object(B, "save_last_launch"), \
                    patch.object(B, "prepare_isolated_config"), patch.object(B, "launch") as launch:
                B.launch_profile("p-one", "model-a", str(workspace), "new")
                first = launch.call_args.kwargs["config_dir"]
                snapshot = (first / "workbench-profile.json").read_bytes()
                B.launch_profile("p-one", "model-b", str(workspace), "new")
                second = launch.call_args.kwargs["config_dir"]
            self.assertNotEqual(first, second)
            self.assertEqual((first / "workbench-profile.json").read_bytes(), snapshot)
            self.assertEqual(json.loads((first / "settings.json").read_text()), {"theme": "dark"})
            self.assertEqual(json.loads(snapshot)["model"], "model-a")


if __name__ == "__main__":
    unittest.main()
