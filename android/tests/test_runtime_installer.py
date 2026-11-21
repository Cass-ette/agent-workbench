import importlib.util
import fcntl
import json
import os
import sys
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "app/src/main/assets/workbench_runtime.py"
sys.path.insert(0, str(source.parent))
spec = importlib.util.spec_from_file_location("runtime", source)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

class RuntimeTests(unittest.TestCase):
    def test_versioned_bundle_uses_persistent_legacy_state_and_its_own_helpers(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / "runtime"
            bundle = state / "releases/build-one"
            bundle.mkdir(parents=True)
            (bundle / "runtime_setup.sh").write_text('bash "$(dirname "$0")/helper.sh"\n')
            (bundle / "helper.sh").write_text('echo RELEASE_ONE\n')
            (state / "runtime_setup.sh").write_text('echo WRONG_LEGACY_RECIPE; exit 9\n')
            installer = R.RuntimeInstaller(state, bundle=bundle)
            installer.start(["codex"])
            result = self.wait_done(installer)
            self.assertEqual(result["state"], "ready")
            self.assertIn("RELEASE_ONE", result["log"])
            self.assertNotIn("WRONG_LEGACY_RECIPE", result["log"])
            self.assertEqual(R.RuntimeInstaller(state).status()["state"], "ready")
            self.assertFalse((bundle / "status.json").exists())

    def wait_done(self, installer):
        limit = time.time() + 5
        while time.time() < limit:
            state = installer.status()
            if state["state"] != "running": return state
            time.sleep(0.02)
        self.fail("Installer did not finish")

    def test_persistent_success_deduplicates_install_and_strips_model_credentials(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"ANTHROPIC_API_KEY": "private-test-key"}):
            p = Path(root)
            (p / "runtime_setup.sh").write_text('set -e\necho "WB_STAGE:test-stage"\n[[ -z "${ANTHROPIC_API_KEY:-}" ]]\necho "$*" >> "'+str(p / 'calls')+'"\nsleep 0.2\n')
            first = R.RuntimeInstaller(p)
            a = first.start(["codex", "codex", "claude"])
            b = R.RuntimeInstaller(p).start(["claude"])
            self.assertEqual(a["pid"], b["pid"])
            result = self.wait_done(first)
            self.assertEqual(result["state"], "ready")
            self.assertEqual((p / "calls").read_text().splitlines(), ["claude codex"])
            self.assertNotIn("private-test-key", result["log"])
            self.assertEqual((p / "status.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(R.RuntimeInstaller(p).status()["state"], "ready")

    def test_failure_keeps_diagnostics_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root); script = p / "runtime_setup.sh"
            script.write_text("echo 'download failed'; exit 17\n")
            installer = R.RuntimeInstaller(p)
            installer.start(["codex"])
            result = self.wait_done(installer)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["exit_code"], 17)
            self.assertIn("download failed", result["log"])
            script.write_text("exit 0\n")
            installer.start(["codex"])
            self.assertEqual(self.wait_done(installer)["state"], "ready")

    def test_no_arbitrary_install_command(self):
        with tempfile.TemporaryDirectory() as root:
            installer = R.RuntimeInstaller(root)
            for choice in ([], ["codex; touch bad"], "claude", ["bash"]):
                with self.assertRaises(ValueError): installer.start(choice)
            with self.assertRaises(ValueError): installer.start(["codex"], "other")

    def test_vm_can_be_installed_without_debian_or_agent_clients(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / "runtime_setup.sh").write_text('test "$#" -eq 1 && test "$1" = --vm\n')
            installer = R.RuntimeInstaller(p)
            installer.start(None, "vm")
            status = self.wait_done(installer)
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["environment"], "vm")

    def test_vm_install_can_install_both_agent_clients(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / "runtime_setup.sh").write_text('test "$#" -eq 3 && test "$1" = --vm && test "$2" = claude && test "$3" = codex\n')
            installer = R.RuntimeInstaller(p)
            installer.start(["codex", "claude"], "vm")
            status = self.wait_done(installer)
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["environment"], "vm")
            self.assertEqual(status["clients"], ["claude", "codex"])

    def test_recipe_is_not_changed_by_an_apk_refresh_during_install(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            script = p / "runtime_setup.sh"
            script.write_text("echo WB_STAGE:snapshot-ready\nsleep 0.3\necho ORIGINAL_RECIPE\n")
            installer = R.RuntimeInstaller(p)
            installer.start(["codex"])
            script.write_text("echo REPLACED_RECIPE; exit 19\n")
            result = self.wait_done(installer)
            self.assertEqual(result["state"], "ready")
            self.assertIn("ORIGINAL_RECIPE", result["log"])
            self.assertNotIn("REPLACED_RECIPE", result["log"])
            self.assertEqual(list(p.glob("recipe-*.sh")), [])

    def test_child_reaped_before_final_status_is_not_reported_as_interrupted(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            installer = R.RuntimeInstaller(p)
            installer.save({"state": "running", "pid": 12345})
            with (p / "install.lock").open("a") as lock, patch.object(R.os, "kill", side_effect=ProcessLookupError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(installer.status()["state"], "running")
            with patch.object(R.os, "kill", side_effect=ProcessLookupError):
                self.assertEqual(installer.status()["state"], "interrupted")

if __name__ == "__main__": unittest.main()
