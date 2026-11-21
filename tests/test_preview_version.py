import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("preview_version", ROOT / "scripts/prepare-preview-version.py")
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)


class PreviewVersionTests(unittest.TestCase):
    def test_all_installer_versions_stay_in_sync_and_increase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("Resources/Info.plist", "windows/package.json", "windows/package-lock.json",
                         "windows/src-tauri/tauri.conf.json", "windows/src-tauri/Cargo.toml",
                         "windows/src-tauri/Cargo.lock", "android/app/build.gradle.kts"):
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, destination)
            old = plistlib.loads((root / "Resources/Info.plist").read_bytes())
            versions = SCRIPT.prepare(root, 100)
            info = plistlib.loads((root / "Resources/Info.plist").read_bytes())
            self.assertEqual(info["CFBundleShortVersionString"], SCRIPT.bumped(old["CFBundleShortVersionString"], 100))
            self.assertEqual(int(info["CFBundleVersion"]), int(old["CFBundleVersion"]) + 100)
            for name in ("package.json", "package-lock.json", "src-tauri/tauri.conf.json"):
                self.assertEqual(json.loads((root / "windows" / name).read_text())["version"], versions["windows"])
            lock = json.loads((root / "windows/package-lock.json").read_text())
            self.assertEqual(lock["packages"][""]["version"], versions["windows"])
            for name in ("Cargo.toml", "Cargo.lock"):
                self.assertIn('name = "windows"\nversion = "' + versions["windows"] + '"', (root / "windows/src-tauri" / name).read_text())
            self.assertIn('versionName = "' + versions["android"] + '"', (root / "android/app/build.gradle.kts").read_text())
            self.assertIn('versionCode = ' + str(versions["android_build"]), (root / "android/app/build.gradle.kts").read_text())
            with self.assertRaises(ValueError):
                SCRIPT.prepare(root, 0)
