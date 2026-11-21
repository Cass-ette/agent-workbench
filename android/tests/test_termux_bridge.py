import base64
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

path = Path(__file__).resolve().parents[1] / "app/src/main/assets/termux_bridge.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
spec = importlib.util.spec_from_file_location("bridge", path)
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class BridgeTests(unittest.TestCase):
    def test_history_restore_resumes_matching_config_and_forks_changed_model(self):
        session_id = "01a1ec86-bf14-70f2-8144-874b179beb72"
        for client in ("codex", "claude"):
            with self.subTest(client=client), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "projects/example"
                workspace.mkdir(parents=True)
                source_home = root / ".local/share/agent-workbench" / client / "source-scope"
                source_home.mkdir(parents=True)
                (source_home / "workbench-profile.json").write_text(json.dumps({
                    "profile_id": "profile-one", "gateway": "https://gateway.test", "model": "model-a",
                    "key_fingerprint": B.hashlib.sha256(b"test-private-key").hexdigest(),
                }))
                if client == "codex":
                    history_file = source_home / "sessions/2026/11/21" / f"rollout-test-{session_id}.jsonl"
                    history_file.parent.mkdir(parents=True)
                    rows = [
                        {"type": "session_meta", "payload": {"id": session_id, "cwd": str(workspace)}},
                        {"type": "turn_context", "payload": {"model": "model-a"}},
                        {"type": "response_item", "payload": {"role": "user", "content": "continue this"}},
                    ]
                else:
                    history_file = source_home / "projects/project" / f"{session_id}.jsonl"
                    history_file.parent.mkdir(parents=True)
                    rows = [
                        {"type": "user", "sessionId": session_id, "cwd": str(workspace),
                         "message": {"content": "continue this"}},
                        {"type": "assistant", "sessionId": session_id,
                         "message": {"model": "model-a"}},
                    ]
                history_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
                history, codex_roots, claude_roots = B.history_context(root)
                (source,) = history.scan_history(codex_roots, claude_roots)
                base = dict(client=client, mode="resume", profile_id="profile-one", model="model-a",
                            key="test-private-key", gateway="https://gateway.test", workspace=str(workspace),
                            history_ref=source["history_ref"], history_id=session_id)
                with patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/usr/bin/" + name):
                    args, env, _ = B.launch_spec(base, root)
                    self.assertEqual(Path(env["CODEX_HOME"] if client == "codex" else env["CLAUDE_CONFIG_DIR"]), source_home)
                    self.assertIn(("codex resume " if client == "codex" else "--resume ") + session_id, args[-1])
                    self.assertNotIn("fork " + session_id, args[-1])
                    self.assertNotIn("--fork-session", args[-1])
                    B.launch_spec(base, root)
                    self.assertEqual(list(source_home.parent.glob("branch-*")), [])
                    _, original_env, _ = B.launch_spec({**base, "history_action": "original"}, root)
                    home_var = "CODEX_HOME" if client == "codex" else "CLAUDE_CONFIG_DIR"
                    self.assertEqual(Path(original_env[home_var]), source_home)
                    with self.assertRaises(B.HistoryUnavailable):
                        B.launch_spec({**base, "key": "replacement-key", "history_action": "original"}, root)
                    self.assertEqual(list(source_home.parent.glob("branch-*")), [])
                    _, branch_env, _ = B.launch_spec({**base, "history_action": "fork"}, root)
                    self.assertNotEqual(Path(branch_env[home_var]), source_home)

                    changed_args, changed_env, _ = B.launch_spec({**base, "model": "model-b"}, root)
                changed_home = Path(changed_env["CODEX_HOME"] if client == "codex" else changed_env["CLAUDE_CONFIG_DIR"])
                self.assertNotEqual(changed_home, source_home)
                if client == "codex":
                    self.assertIn("codex fork " + session_id, changed_args[-1])
                else:
                    self.assertIn("--resume " + session_id + " --fork-session", changed_args[-1])
                with patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/usr/bin/" + name):
                    for change in ({"profile_id": "profile-two"}, {"key": "replacement-key"}):
                        next_args, next_env, _ = B.launch_spec({**base, **change}, root)
                        self.assertNotEqual(Path(next_env["CODEX_HOME"] if client == "codex" else next_env["CLAUDE_CONFIG_DIR"]), source_home)
                self.assertTrue(history_file.exists())

    def test_history_resume_rejects_a_different_project_directory(self):
        with tempfile.TemporaryDirectory() as home:
            source_workspace = Path(home) / "project-a"
            payload = dict(client="codex", mode="resume", profile_id="profile", model="model",
                           key="private", gateway="https://gateway.test", workspace="~/project-b",
                           history_ref="history-ref", history_id="session-id")
            fake_history = SimpleNamespace(resolve_history=lambda *args: {
                "id": "session-id", "workspace": str(source_workspace), "client": "codex",
            })
            with patch.object(B, "history_context", return_value=(fake_history, [], [])):
                with self.assertRaisesRegex(B.HistoryUnavailable, "所属的项目目录"):
                    B.launch_spec(payload, home)

    def test_old_bridge_launch_keeps_its_runner_after_new_bundle_is_installed(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / ".local/share/agent-workbench/runtime"
            old = root / "releases/old"
            newer = root / "releases/new"
            for bundle in (old, newer):
                bundle.mkdir(parents=True)
                (bundle / "debian_runner.py").touch()
            payload = dict(client="codex", model="test", key="dummy", gateway="https://gateway.test")
            with patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/usr/bin/" + name):
                args, env, _ = B.launch_spec(payload, home, bundle=old)
            self.assertIn(str(old / "debian_runner.py"), args[-1])
            self.assertNotIn(str(newer), args[-1])
            self.assertNotIn("WORKBENCH_GATEWAY_KEY", env)

    def test_vm_launch_uses_one_time_private_payload_without_key_in_process_args(self):
        with tempfile.TemporaryDirectory() as home:
            class InstalledVM:
                def __init__(self, root): pass
                def status(self): return {"installed": True}

            payload = dict(client="codex", mode="new", profile_id="vm-profile", model="test-model",
                           key="private-vm-key", gateway="https://gateway.test",
                           workspace="~/projects/vm-project", environment="vm")
            with patch.dict(B.sys.modules, {"linux_vm": SimpleNamespace(LinuxVM=InstalledVM)}), \
                 patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/bin/" + name):
                args, env, workspace = B.launch_spec(payload, home)
            self.assertNotIn(payload["key"], repr(args))
            self.assertNotIn(payload["key"], repr(env))
            self.assertTrue(workspace.endswith("projects/vm-project"))
            launch_file = next((Path(home) / ".local/share/agent-workbench").glob("vm-launch-*.json"))
            self.assertEqual(launch_file.stat().st_mode & 0o777, 0o600)
            launch = json.loads(launch_file.read_text())
            self.assertEqual(launch["key"], payload["key"])
            self.assertEqual(launch["workspace"], str(Path(workspace).resolve()))
            self.assertIn("linux_vm.py", args[-1])
            self.assertIn("; unset", args[-1])

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

    @unittest.skipUnless(shutil.which("ssh-keygen"), "OpenSSH needed for the pairing test")
    def test_ssh_pairing_pins_host_and_remembers_only_after_key_authentication(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home)
            host_identity = root / "host_identity"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_identity)], check=True)
            host_key = " ".join(host_identity.with_suffix(".pub").read_text().split()[:2])
            session_id = "01a1ec86-bf14-70f2-8144-874b179beb72"
            target = {"id": session_id, "platform": "macos", "host_key": host_key, "remember": True}
            computer = root / "computer"
            authorized = computer / ".ssh/authorized_keys"
            authorized.parent.mkdir(parents=True)
            authorized.write_text("existing line without newline")
            authorized.chmod(0o640)
            fake_ssh = root / "ssh"
            fake_ssh.write_text("""#!/bin/sh
for value do
    last=$value
    case "$value" in
        BatchMode=yes) batch=yes ;;
        PubkeyAuthentication=no) password=yes ;;
    esac
done
if [ "$batch" = yes ]; then
    grep -q AgentWorkbench-Android "$HOME/.ssh/authorized_keys" && [ "$REJECT_KEY" != yes ]
elif [ "$password" = yes ]; then
    printf 'PASSWORD'
elif [ "$last" = laptop ]; then
    printf 'INTERACTIVE'
else
    /bin/sh -c "$last"
fi
""")
            fake_ssh.chmod(0o700)
            env = {**os.environ, "HOME": str(computer)}
            script = B.paired_ssh_arguments(str(fake_ssh), root, env, target, "laptop", "user", 2222)
            pairing = root / ".local/share/agent-workbench/ssh" / session_id
            self.assertEqual((pairing / "known_hosts").read_text(), f"[laptop]:2222 {host_key}\n")
            self.assertEqual((pairing / "id_ed25519").stat().st_mode & 0o777, 0o600)
            self.assertIn("StrictHostKeyChecking=yes", shlex.split(script))
            rejected = subprocess.run(["/bin/sh", "-c", script], env={**env, "REJECT_KEY": "yes"},
                                      input="\n", capture_output=True, text=True, check=True)
            self.assertIn("PASSWORD", rejected.stdout)
            self.assertFalse((pairing / "paired").exists())
            installed = subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True, check=True)
            self.assertIn("INTERACTIVE", installed.stdout)
            self.assertTrue((pairing / "paired").exists())
            lines = authorized.read_text().splitlines()
            self.assertEqual(lines[0], "existing line without newline")
            self.assertEqual(sum(line.endswith("AgentWorkbench-Android-" + session_id) for line in lines), 1)
            self.assertEqual(authorized.stat().st_mode & 0o777, 0o640)
            repeat = B.paired_ssh_arguments(str(fake_ssh), root, env, target, "laptop", "user", 2222)
            self.assertNotIn("AuthorizedKeys", repeat)
            self.assertNotIn("BatchMode=yes", repeat)

    def test_debian_shell_uses_same_user_and_workspace_mount_as_agent(self):
        with tempfile.TemporaryDirectory() as home, patch.object(B.shutil, "which", side_effect=lambda name, **kw: "/usr/bin/" + name):
            args, env, workspace = B.launch_spec({
                "client": "debian-shell", "workspace": "~/projects/example",
            }, home)
            self.assertEqual(args[:3], ["/usr/bin/bash", "--noprofile", "--norc"])
            self.assertIn("proot-distro login debian --user workbench", args[-1])
            self.assertIn("--bind " + home + ":" + home, args[-1])
            self.assertIn("--work-dir " + home + "/projects/example", args[-1])
            self.assertEqual(workspace, home + "/projects/example")
            self.assertNotIn("WORKBENCH_GATEWAY_KEY", env)

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
