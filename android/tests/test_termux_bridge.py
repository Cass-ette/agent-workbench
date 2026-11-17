import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

path = Path(__file__).resolve().parents[1] / "app/src/main/assets/termux_bridge.py"
spec = importlib.util.spec_from_file_location("bridge", path)
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class BridgeTests(unittest.TestCase):
    def test_key_is_only_in_child_environment_and_configs_are_isolated(self):
        with tempfile.TemporaryDirectory() as home:
            payload = dict(client="codex", mode="new", profile_id="one", model="model; echo wrong", key="test-private-value",
                           gateway="https://gateway.test", workspace="~/project with space")
            with patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/bin/" + name):
                args, env, workspace = B.launch_spec(payload, home)
                _, other, _ = B.launch_spec({**payload, "profile_id": "two"}, home)
            self.assertNotIn(payload["key"], json.dumps(args))
            self.assertEqual(env["WORKBENCH_GATEWAY_KEY"], payload["key"])
            self.assertNotEqual(env["CODEX_HOME"], other["CODEX_HOME"])
            config = Path(env["CODEX_HOME"]) / "config.toml"
            self.assertNotIn(payload["key"], config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertIn("'model; echo wrong'", args[-1])
            self.assertTrue(workspace.endswith("project with space"))

    def test_new_continue_resume_are_distinct(self):
        with tempfile.TemporaryDirectory() as home, patch.object(B.shutil, "which", side_effect=lambda n, **kw: "/bin/" + n):
            payload = dict(client="codex", profile_id="test", model="test", key="dummy", gateway="https://a.test")
            commands = [B.launch_spec({**payload, "mode": mode}, home)[0][-1] for mode in ("new", "continue", "resume")]
            self.assertNotIn(" resume ", commands[0])
            self.assertIn("resume --last", commands[1])
            self.assertIn(" resume ", commands[2])
            self.assertNotIn("--last", commands[2])

    def test_missing_host_cli_uses_debian_without_putting_key_in_argv(self):
        with tempfile.TemporaryDirectory() as home:
            runtime = Path(home) / ".local/share/agent-workbench/runtime"
            runtime.mkdir(parents=True)
            (runtime / "debian_runner.py").touch()
            payload = dict(client="claude", mode="new", profile_id="debian", model="model-name",
                           key="test-private-value", gateway="https://gateway.test", workspace="~/project")
            def which(name, **_):
                if name == "claude": return None
                if name == "proot-distro": return "/usr/bin/proot-distro"
                return "/bin/" + name
            with patch.object(B.shutil, "which", side_effect=which):
                args, env, workspace = B.launch_spec(payload, home)
            command = json.dumps(args)
            self.assertEqual(args[:3], ["/bin/bash", "--noprofile", "--norc"])
            self.assertIn("/usr/bin/proot-distro login debian", args[-1])
            self.assertIn("--user workbench", args[-1])
            self.assertNotIn("--shared-home", args[-1])
            self.assertNotIn(payload["key"], command)
            self.assertNotIn(payload["key"], json.dumps(env))
            secret = next(Path(home).rglob("launch-*.json"))
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            launch = json.loads(secret.read_text())
            self.assertEqual(launch["environment"]["ANTHROPIC_API_KEY"], payload["key"])
            self.assertEqual(launch["workspace"], str(Path(home) / "project"))
            self.assertIn(str(Path(home)), args[-1])
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", launch["environment"])
            self.assertIn("--bare", launch["args"])
            self.assertIn(str(Path(home) / "project"), launch["args"])
            for name in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
                self.assertEqual(launch["environment"][name], payload["model"])

    def test_claude_settings_survive_restart_and_other_provider_auth_is_removed(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "other-login", "CLAUDE_CODE_USE_VERTEX": "1"}), \
             patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/bin/" + name):
            payload = dict(client="claude", model="test", key="test-key", gateway="https://gateway.test")
            _, env, _ = B.launch_spec(payload, home)
            settings = Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"
            settings.write_text('{"theme":"dark"}\n')
            args, next_env, _ = B.launch_spec(payload, home)
            self.assertEqual(json.loads(settings.read_text()), {"theme": "dark"})
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", next_env)
            self.assertNotIn("CLAUDE_CODE_USE_VERTEX", next_env)
            self.assertNotIn(payload["key"], json.dumps(args))

    def test_codex_sandbox_compatibility_requires_explicit_choice_and_keeps_approvals(self):
        with tempfile.TemporaryDirectory() as home, patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/bin/" + name):
            runtime = Path(home) / ".local/share/agent-workbench/runtime"
            runtime.mkdir(parents=True)
            (runtime / "debian_runner.py").touch()
            payload = dict(client="codex", model="test", key="dummy", gateway="https://gateway.test")
            B.launch_spec(payload, home)
            first = next(Path(home).rglob("launch-*.json"))
            self.assertNotIn("danger-full-access", json.loads(first.read_text())["args"])
            first.unlink()
            B.launch_spec({**payload, "codex_compatibility": True}, home)
            command = json.loads(next(Path(home).rglob("launch-*.json")).read_text())["args"]
            self.assertIn("danger-full-access", command)
            self.assertEqual(command[command.index("--ask-for-approval") + 1], "on-request")
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_ssh_uses_argv_and_never_receives_gateway_credentials(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home, "WORKBENCH_GATEWAY_KEY": "do-not-forward"}), \
             patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/usr/bin/" + name):
            args, env, workspace = B.launch_spec({
                "client": "ssh", "ssh": {"host": "laptop", "user": "ASUS", "port": 22},
                "key": "phone-gateway-key", "gateway": "https://gateway.test",
            }, home)
            self.assertEqual(args[0], "/usr/bin/bash")
            self.assertIn("ssh", args[-1])
            self.assertIn("StrictHostKeyChecking=ask", args[-1])
            self.assertIn("-l ASUS laptop", args[-1])
            self.assertNotIn("do-not-forward", str(env))
            self.assertNotIn("phone-gateway-key", str(env))
            self.assertEqual(workspace, home)

    def test_ssh_rejects_command_injection_hosts_and_users(self):
        with tempfile.TemporaryDirectory() as home, patch.object(B.shutil, "which", return_value="/usr/bin/ssh"):
            for target in (
                {"host": "laptop; touch /tmp/pwned"},
                {"host": "laptop", "user": "user && whoami"},
                {"host": "laptop", "port": 0},
            ):
                with self.assertRaises(ValueError):
                    B.launch_spec({"client": "ssh", "ssh": target}, home)

    def test_loopback_and_auth_are_required(self):
        with self.assertRaises(ValueError): B.BridgeServer(("0.0.0.0", 0), "a" * 64)
        server = B.BridgeServer(("127.0.0.1", 0), "a" * 64)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:" + str(server.server_port)
        try:
            with self.assertRaises(HTTPError) as error: urlopen(url + "/health", timeout=2)
            self.assertEqual(error.exception.code, 401)
            request = Request(url + "/health", headers={"Authorization": "Bearer " + "a" * 64})
            with urlopen(request, timeout=2) as response:
                self.assertEqual(json.load(response)["service"], "agent-workbench-termux")
        finally:
            server.shutdown(); server.server_close()

    def test_usage_endpoint_returns_tokens_without_session_text(self):
        with tempfile.TemporaryDirectory() as home:
            log = Path(home) / ".local/share/agent-workbench/claude/profile/projects/session.jsonl"
            log.parent.mkdir(parents=True)
            rows = [
                {"type": "assistant", "message": {"id": "a1", "usage": {
                    "input_tokens": 100, "output_tokens": 25,
                    "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}},
                 "secret_prompt": "must not leave the bridge"},
                {"type": "result", "total_cost_usd": 0.0125},
            ]
            log.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            result = B.usage_snapshot(home)
            self.assertEqual(result["input_tokens"], 115)
            self.assertEqual(result["output_tokens"], 25)
            self.assertEqual(result["requests"], 1)
            self.assertEqual(result["estimated_cost"], 0.0125)
            self.assertNotIn("secret_prompt", json.dumps(result))

    def test_real_pty_echoes_and_interrupt_returns_to_shell(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            session = B.Session(dict(client="shell", workspace=home))
            try:
                os.write(session.fd, b"printf 'PTY_OK\\n'\n")
                limit = time.time() + 4
                while time.time() < limit and b"PTY_OK" not in base64.b64decode(session.snapshot(0)["data"]): time.sleep(0.05)
                self.assertIn(b"PTY_OK", base64.b64decode(session.snapshot(0)["data"]))
                os.write(session.fd, b"\x03")
                time.sleep(0.1)
                self.assertIsNone(session.exit_code)
            finally:
                session.stop()


if __name__ == "__main__": unittest.main()
