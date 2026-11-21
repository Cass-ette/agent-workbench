import base64
import importlib.util
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import tempfile
import os
import sys
import unittest
from unittest.mock import Mock, patch

source = Path(__file__).resolve().parents[1] / "app/src/main/assets/linux_vm.py"
spec = importlib.util.spec_from_file_location("linux_vm_tests", source)
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


class VMTests(unittest.TestCase):
    def test_userspace_abi_overrides_64_bit_kernel_for_32_bit_termux(self):
        for kernel, userspace in (("aarch64", "arm"), ("x86_64", "i686")):
            with self.subTest(kernel=kernel), \
                 patch.object(V.os, "uname", return_value=SimpleNamespace(machine=kernel)), \
                 patch.object(V.struct, "calcsize", return_value=4):
                self.assertEqual(V.LinuxVM.host_architecture(), userspace)

    def test_fresh_supported_devices_can_install_vm_without_legacy_arm_metadata(self):
        for architecture in ("aarch64", "x86_64"):
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as home:
                vm = V.LinuxVM(home)
                with patch.object(vm, "host_architecture", return_value=architecture), \
                     patch.object(vm, "kvm_available", return_value=False):
                    status = vm.status()
                self.assertTrue(status["supported"])
                self.assertFalse(status["installed"])
                self.assertFalse(vm.root.exists())

    def test_legacy_arm_vm_is_preserved_and_not_offered_as_native_x86_vm(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            vm.root.mkdir(parents=True)
            metadata = vm.root / "installed.json"
            metadata.write_text('{"version":"legacy"}')
            with patch.object(vm, "host_architecture", return_value="x86_64"), \
                 patch.object(vm, "kvm_available", return_value=False):
                status = vm.status()
            self.assertTrue(status["installed"])
            self.assertFalse(status["supported"])
            self.assertEqual(metadata.read_text(), '{"version":"legacy"}')
            with patch.object(vm, "host_architecture", return_value="x86_64"), \
                 patch.object(V.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "数据已保留"):
                    vm.start()
                popen.assert_not_called()

    def test_installer_uses_release_branch_for_verified_image(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            with patch.object(vm, "host_architecture", return_value="aarch64"), \
                 patch.object(V.shutil, "which", return_value="present"), \
                 patch.object(V, "checked_download", side_effect=RuntimeError("download boundary")) as download:
                with self.assertRaisesRegex(RuntimeError, "download boundary"):
                    vm.install()
            self.assertEqual(download.call_args.args[0],
                "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/alpine-virt-3.24.4-aarch64.iso")
            self.assertEqual(download.call_args.args[2], V.PLATFORMS["aarch64"]["sha256"])
            provision = vm._provision("ssh-ed25519 test")
            self.assertIn("/alpine/v3.24/main", provision)
            self.assertNotIn("__WB_APK_REPOSITORIES__", provision)
            subprocess.run(["sh", "-n"], input=provision, text=True, check=True)

    def test_host_architecture_selects_native_guest_and_matching_smoke_loader(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            with patch.object(V.os, "uname", return_value=SimpleNamespace(machine="x86_64")):
                platform = vm.platform()
            self.assertEqual(platform["qemu"], "qemu-system-x86_64")
            self.assertEqual(platform["package"], "qemu-system-x86-64-headless")
            self.assertEqual(platform["iso_arch"], "x86_64")
            self.assertEqual(platform["sha256"], "157d353f8e54252cad2d9812a0d56bb7fa2231fc154a110e1d180d7480ef8b4f")
            smoke = V.DOCKER_SMOKE.replace("__MUSL_LOADER__", platform["musl_loader"])
            self.assertIn("lib/ld-musl-x86_64.so.1", smoke)

    def test_x86_64_guest_uses_matching_qemu_console(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            process = SimpleNamespace(poll=lambda: None)
            with patch.object(V.os, "uname", return_value=SimpleNamespace(machine="x86_64")), \
                 patch.object(vm, "kvm_available", return_value=False), patch.object(V.time, "sleep"), \
                 patch.object(V.subprocess, "Popen", return_value=process) as popen:
                vm.start(installer=True)
            args = popen.call_args.args[0]
            self.assertEqual(args[0], "qemu-system-x86_64")
            self.assertEqual(args[args.index("-machine") + 1], "q35")
            self.assertEqual(args[args.index("-cpu") + 1], "max")
            self.assertIn("console=ttyS0", args[args.index("-append") + 1])

    def test_unsupported_architecture_fails_before_touching_vm_files(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            with patch.object(V.os, "uname", return_value=SimpleNamespace(machine="armv7l")):
                with self.assertRaisesRegex(ValueError, "SSH"):
                    vm.install()
            self.assertFalse(vm.root.exists())

    def test_agent_install_script_is_valid_and_pins_cli_versions(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            vm.root.mkdir(parents=True)
            installation_path = vm.root / "installed.json"
            installation_path.write_text('{"version":"test"}')
            with patch.object(vm, "pid", return_value=123), patch.object(vm, "start"), \
                 patch.object(vm, "wait_ready"), patch.object(vm, "ssh", return_value=SimpleNamespace(returncode=0)) as ssh:
                vm.install_agents(["codex", "claude"])
            script = ssh.call_args.args[0]
            subprocess.run(["bash", "-n"], input=script, text=True, check=True)
            self.assertIn("@openai/codex@0.169.0", script)
            self.assertIn("@anthropic-ai/claude-code@2.1.326", script)
            self.assertIn("p.version===process.argv[2]", script)
            self.assertIn("npm install -g --no-audit --no-fund", script)
            encoded = re.search(r"base64 -d > /usr/local/bin/wb-agent-runner.py <<'__WB_RUNNER__'\n([^\n]+)", script)[1]
            self.assertEqual(base64.b64decode(encoded).decode(), V.AGENT_RUNNER)
            metadata = json.loads(installation_path.read_text())
            self.assertEqual(metadata["clients"], ["claude", "codex"])

    def test_runner_uses_full_guest_permissions_for_codex_and_valid_claude_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            workspace = projects / "sample"
            workspace.mkdir(parents=True)
            home = root / "guest-home"
            home.mkdir()
            prior_cwd = Path.cwd()

            def run(client):
                payload_file = root / (client + ".json")
                payload_file.write_text(json.dumps({
                    "client": client, "mode": "new", "scope": "a" * 24,
                    "workspace": str(workspace), "model": "test-model",
                    "gateway": "https://gateway.test", "key": "private-test-key",
                }))
                script = V.AGENT_RUNNER.replace('Path("/projects")', "Path(" + repr(str(projects)) + ")")
                captured = {}

                class ExecComplete(Exception):
                    pass

                def capture_exec(executable, args, env):
                    captured.update(executable=executable, args=args, env=env)
                    raise ExecComplete()

                with patch.object(V.Path, "home", return_value=home), \
                     patch.object(V.shutil, "which", return_value="/usr/bin/" + client), \
                     patch.object(V.os, "execvpe", side_effect=capture_exec), \
                     patch.object(V.sys, "argv", ["runner.py", str(payload_file)]):
                    try:
                        exec(compile(script, "wb-agent-runner.py", "exec"), {})
                    except ExecComplete:
                        pass
                    else:
                        self.fail("Agent runner did not hand off to the CLI")
                self.assertFalse(payload_file.exists())
                self.assertNotIn("private-test-key", json.dumps(captured["args"]))
                self.assertEqual(captured["env"].get("WORKBENCH_GATEWAY_KEY") or
                                 captured["env"].get("ANTHROPIC_API_KEY"), "private-test-key")
                return captured

            try:
                codex = run("codex")
                self.assertIn("danger-full-access", codex["args"])
                self.assertIn("on-request", codex["args"])
                self.assertNotIn("private-test-key", (Path(codex["env"]["CODEX_HOME"]) / "config.toml").read_text())
                claude = run("claude")
                settings = Path(claude["env"]["CLAUDE_CONFIG_DIR"]) / "settings.json"
                self.assertEqual(json.loads(settings.read_text()), {})
                self.assertEqual(settings.read_text(), "{}\n")
                self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
            finally:
                os.chdir(prior_cwd)

    def test_agent_requires_project_under_phone_projects_and_guest_mount(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            vm.root.mkdir(parents=True)
            (vm.root / "installed.json").write_text('{"version":"test"}')
            projects = Path(home) / "projects"
            workspace = projects / "sample"
            workspace.mkdir(parents=True)
            outside = Path(home) / "outside"
            outside.mkdir()
            (projects / "escape").symlink_to(outside, target_is_directory=True)
            payload = {"client": "codex", "workspace": str(outside), "model": "m", "key": "k",
                       "gateway": "https://gateway.test"}
            with patch.object(vm, "start") as start:
                with self.assertRaisesRegex(ValueError, "~/projects"):
                    vm.run_agent(payload)
                start.assert_not_called()
            payload["workspace"] = str(projects / "escape")
            with patch.object(vm, "start") as start:
                with self.assertRaisesRegex(ValueError, "~/projects"):
                    vm.run_agent(payload)
                start.assert_not_called()

    def test_agent_launch_keeps_key_out_of_ssh_arguments_and_checks_shared_mount(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            (vm.root / "installed.json").parent.mkdir(parents=True)
            (vm.root / "installed.json").write_text('{"version":"test"}')
            workspace = Path(home) / "projects/sample"
            workspace.mkdir(parents=True)
            response = SimpleNamespace(returncode=0)
            agent_exit = SimpleNamespace(returncode=7)
            with patch.object(vm, "start"), patch.object(vm, "wait_ready"), \
                 patch.object(vm, "ssh", side_effect=[response, response, response, agent_exit, response]) as ssh:
                result = vm.run_agent({"client": "codex", "workspace": str(workspace), "model": "m",
                                       "key": "private-test-key", "gateway": "https://gateway.test"})
            self.assertEqual(result, 7)
            calls = ssh.call_args_list
            self.assertIn("test -d /projects/sample", calls[0].args[0])
            self.assertIn("command -v codex", calls[1].args[0])
            self.assertNotIn("private-test-key", repr([call.args for call in calls]))
            self.assertIn(b"private-test-key", calls[2].kwargs["input"])

    def test_long_provision_script_uses_bounded_tty_lines(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            command = "echo '中文安装步骤'\n" * 500
            with patch.object(V.socket, "socket", return_value=connection), \
                 patch.object(V.time, "sleep"), \
                 patch.object(vm, "_collect", side_effect=[b"localhost:~# ", b"\n__WB_ECHO_OFF__\n", b"\n__WB_DONE_0__\n"]):
                vm.console(command)
            sent = [call.args[0] for call in connection.sendall.call_args_list]
            first = next(i for i, data in enumerate(sent) if data.startswith(b"cat >")) + 1
            last = sent.index(b"__WB_PAYLOAD__\r")
            self.assertGreater(last - first, 10)
            self.assertTrue(all(len(data) <= 513 for data in sent[first:last]))
            self.assertEqual(base64.b64decode(b"".join(sent[first:last])), command.encode())

    def test_unowned_disk_is_preserved(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            (vm.root / "boot").mkdir(parents=True)
            for name in ("vmlinuz-virt", "initramfs-virt"):
                (vm.root / "boot" / name).touch()
            disk = vm.root / "disk.raw"
            disk.write_bytes(b"existing-system-data")
            with patch.object(V.os, "uname", return_value=SimpleNamespace(machine="aarch64")), \
                 patch.object(V.shutil, "which", return_value="available"), \
                 patch.object(V, "checked_download"), patch.object(V.subprocess, "run"), \
                 patch.object(vm, "start") as start:
                with self.assertRaisesRegex(ValueError, "不会格式化"):
                    vm.install()
                start.assert_not_called()
            self.assertEqual(disk.read_bytes(), b"existing-system-data")

    def test_failed_container_check_does_not_mark_installed(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            vm.root.mkdir(parents=True)
            prepared = vm.root / "prepared.json"
            prepared.write_text('{"version":"test"}')
            with patch.object(vm, "start"), patch.object(vm, "wait_ready"), \
                 patch.object(vm, "ssh", side_effect=V.subprocess.CalledProcessError(1, "docker")):
                with self.assertRaises(V.subprocess.CalledProcessError): vm._verify()
            self.assertTrue(prepared.exists())
            self.assertFalse(vm.status()["installed"])

    def test_ssh_disconnect_alone_does_not_mean_vm_stopped(self):
        with tempfile.TemporaryDirectory() as home:
            vm = V.LinuxVM(home)
            with patch.object(vm, "pid", return_value=123), \
                 patch.object(vm, "ssh", return_value=SimpleNamespace(returncode=255)), \
                 patch.object(V.time, "monotonic", side_effect=[0, 100]):
                with self.assertRaisesRegex(ValueError, "尚未关机"):
                    vm.stop()


if __name__ == "__main__":
    unittest.main()
