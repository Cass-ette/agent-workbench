import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-update-manifest.py"
SPEC = importlib.util.spec_from_file_location("build_update_manifest", SCRIPT)
MANIFEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANIFEST)


class UpdateManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts = self.root / "artifacts"
        self.output = self.root / "release-assets"
        self._make_artifacts()

    def tearDown(self):
        self.temp.cleanup()

    def _make_artifacts(self):
        mac_dir = self.artifacts / "macos-agent-workbench"
        mac_dir.mkdir(parents=True)
        mac_zip = mac_dir / "Agent-Workbench-macOS-universal.zip"
        info = plistlib.dumps({
            "CFBundleIdentifier": "com.cassette.agentworkbench",
            "CFBundleShortVersionString": "0.1.2",
        })
        with zipfile.ZipFile(mac_zip, "w") as archive:
            archive.writestr("Agent Workbench.app/Contents/Info.plist", info)

        windows_dir = self.artifacts / "windows-agent-workbench-installer"
        windows_dir.mkdir()
        (windows_dir / "Agent-Workbench-setup.exe").write_bytes(b"windows installer")
        (windows_dir / "version.txt").write_text("0.1.4\n", encoding="utf-8")

        android_dir = self.artifacts / "android-agent-workbench-preview-apk"
        android_dir.mkdir()
        (android_dir / "workbench.apk").write_bytes(b"signed apk")
        (android_dir / "output-metadata.json").write_text(json.dumps({
            "applicationId": "com.cassette.workbench",
            "variantName": "debug",
            "elements": [{"filters": [], "versionCode": 21, "versionName": "0.1.19"}],
        }), encoding="utf-8")

    def test_builds_manifest_and_hashes_copied_packages(self):
        manifest = MANIFEST.build_manifest(self.artifacts, " Test release notes ", "workbench-preview-123-1", self.output)

        self.assertTrue(all(p["channel"] == "preview" for p in manifest["packages"]))
        self.assertEqual(manifest["packages"][-1]["signing"], "development")
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["notes"], "Test release notes")
        self.assertEqual(
            [(item["platform"], item["version"]) for item in manifest["packages"]],
            [("macos", "0.1.2"), ("windows", "0.1.4"), ("android", "0.1.19")],
        )
        for item in manifest["packages"]:
            copied = self.output / item["url"].rsplit("/", 1)[-1]
            self.assertEqual(copied.stat().st_size, item["size"])
            self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(), item["sha256"])
        self.assertEqual(len((self.output / "SHA256SUMS.txt").read_text().splitlines()), 3)

    def test_stable_requires_explicit_channel_and_release_variant(self):
        preview = self.artifacts / "android-agent-workbench-preview-apk"
        stable = self.artifacts / "android-agent-workbench-stable"
        preview.rename(stable)
        metadata = stable / "output-metadata.json"
        data = json.loads(metadata.read_text())
        data["variantName"] = "release"
        data["elements"][0]["versionName"] = "1.0.0"
        metadata.write_text(json.dumps(data))
        self._set_desktop_stable_versions()
        manifest = MANIFEST.build_manifest(self.artifacts, "stable notes", "workbench-v1.0.0", self.output, "stable")
        self.assertTrue(all(p["channel"] == "stable" for p in manifest["packages"]))
        self.assertEqual(manifest["packages"][-1]["signing"], "release")

    def _set_desktop_stable_versions(self):
        with zipfile.ZipFile(self.artifacts / "macos-agent-workbench" / "Agent-Workbench-macOS-universal.zip", "w") as archive:
            archive.writestr("Agent Workbench.app/Contents/Info.plist", plistlib.dumps({
                "CFBundleIdentifier": "com.cassette.agentworkbench", "CFBundleShortVersionString": "1.0.0",
            }))
        (self.artifacts / "windows-agent-workbench-installer" / "version.txt").write_text("1.0.0")

    def test_stable_migration_offers_same_apk_to_both_installed_build_types(self):
        self._set_desktop_stable_versions()
        stable = self.artifacts / "android-agent-workbench-stable"
        (self.artifacts / "android-agent-workbench-preview-apk").rename(stable)
        metadata = stable / "output-metadata.json"
        data = json.loads(metadata.read_text())
        data["variantName"] = "release"
        data["elements"][0].update(versionName="1.0.0", versionCode=95)
        metadata.write_text(json.dumps(data))
        result = MANIFEST.build_manifest(self.artifacts, "stable", "workbench-v1.0.0", self.output,
                                         "stable", android_development_compatible=True)
        packages = [p for p in result["packages"] if p["platform"] == "android"]
        self.assertEqual({p["signing"] for p in packages}, {"release", "development"})
        self.assertEqual({p["url"] for p in packages}, {packages[0]["url"]})
        self.assertEqual({p["sha256"] for p in packages}, {packages[0]["sha256"]})
        self.assertEqual(len((self.output / "SHA256SUMS.txt").read_text().splitlines()), 3)

    def test_stable_rejects_mismatched_installer_versions(self):
        stable = self.artifacts / "android-agent-workbench-stable"
        (self.artifacts / "android-agent-workbench-preview-apk").rename(stable)
        metadata = stable / "output-metadata.json"
        data = json.loads(metadata.read_text())
        data["variantName"] = "release"
        metadata.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "match the stable release tag"):
            MANIFEST.build_manifest(self.artifacts, "notes", "workbench-v1.0.0", self.output, "stable")

    def test_preview_cannot_claim_stable_signing_migration(self):
        with self.assertRaisesRegex(ValueError, "migration alias"):
            MANIFEST.build_manifest(self.artifacts, "notes", "workbench-preview-123-1", self.output,
                                    android_development_compatible=True)

    def test_version_controls_status_even_when_release_mode_differs(self):
        info_path = self.artifacts / "macos-agent-workbench" / "Agent-Workbench-macOS-universal.zip"
        with zipfile.ZipFile(info_path, "w") as archive:
            archive.writestr("Agent Workbench.app/Contents/Info.plist", plistlib.dumps({
                "CFBundleIdentifier": "com.cassette.agentworkbench", "CFBundleShortVersionString": "1.0.0",
            }))
        manifest = MANIFEST.build_manifest(self.artifacts, "notes", "workbench-preview-123-1", self.output)
        self.assertEqual(manifest["packages"][0]["channel"], "stable")
        self.assertEqual(manifest["packages"][1]["channel"], "preview")

    def test_rejects_invalid_release_tag_before_creating_output(self):
        with self.assertRaisesRegex(ValueError, "Release tag"):
            MANIFEST.build_manifest(self.artifacts, "notes", "v0.1.2", self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_wrong_android_variant_before_creating_output(self):
        metadata_path = self.artifacts / "android-agent-workbench-preview-apk" / "output-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["variantName"] = "release"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "debug APK"):
            MANIFEST.build_manifest(self.artifacts, "notes", "workbench-preview-123-1", self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
