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
    def test_stable_version_is_exact_and_build_numbers_remain_monotonic(self):
        with tempfile.TemporaryDirectory() as directory:
            preview, stable = Path(directory) / "preview", Path(directory) / "stable"
            for root in (preview, stable):
                for name in ("Resources/Info.plist", "windows/package.json", "windows/package-lock.json",
                             "windows/src-tauri/tauri.conf.json", "windows/src-tauri/Cargo.toml",
                             "windows/src-tauri/Cargo.lock", "android/app/build.gradle.kts"):
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, destination)
            before = SCRIPT.prepare(preview, 40)
            result = SCRIPT.prepare(stable, 42, "1.0.0")
            self.assertEqual({result[p] for p in ("macos", "windows", "android")}, {"1.0.0"})
            self.assertGreater(result["android_build"], before["android_build"])
            old_info = plistlib.loads((preview / "Resources/Info.plist").read_bytes())
            info = plistlib.loads((stable / "Resources/Info.plist").read_bytes())
            self.assertGreater(int(info["CFBundleVersion"]), int(old_info["CFBundleVersion"]))
            for name in ("package.json", "package-lock.json", "src-tauri/tauri.conf.json"):
                self.assertEqual(json.loads((stable / "windows" / name).read_text())["version"], "1.0.0")
            lock = json.loads((stable / "windows/package-lock.json").read_text())
            self.assertEqual(lock["packages"][""]["version"], "1.0.0")
            for name in ("Cargo.toml", "Cargo.lock"):
                self.assertIn('name = "windows"\nversion = "1.0.0"', (stable / "windows/src-tauri" / name).read_text())
            self.assertIn('versionName = "1.0.0"', (stable / "android/app/build.gradle.kts").read_text())

    def test_invalid_stable_version_does_not_mutate_sources(self):
        for version in ("0.2.0", "1.0.0-preview", "1.0", "1.00.0", "1.0.0\n"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "Stable version"):
                SCRIPT.prepare(Path("does-not-exist"), 42, version)

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
