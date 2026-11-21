import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/publish-update-feed.py"
SPEC = importlib.util.spec_from_file_location("publish_update_feed", SCRIPT)
FEED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FEED)


def package(platform, version, build=0, signing=""):
    return {"platform": platform, "arch": "universal", "version": version, "build": build,
            "signing": signing, "channel": "preview" if version.startswith("0.") else "stable"}


def manifest(packages):
    return {"schema": 1, "notes": "release notes", "packages": packages}


class UpdateFeedTests(unittest.TestCase):
    def test_requires_complete_unique_and_version_classified_manifest(self):
        complete = [package("macos", "0.2.0"), package("windows", "0.2.0"),
                    package("android", "0.2.0", build=20, signing="development")]
        FEED.validate_complete(manifest(complete))
        with self.assertRaisesRegex(ValueError, "complete"):
            FEED.validate_complete(manifest(complete[:2]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            FEED.validate_complete(manifest(complete + [complete[0]]))
        incorrectly_classified = [package("macos", "1.0.0"), package("windows", "0.2.0"), complete[2]]
        incorrectly_classified[0]["channel"] = "preview"
        with self.assertRaisesRegex(ValueError, "maturity"):
            FEED.validate_complete(manifest(incorrectly_classified))

    def test_old_build_cannot_move_feed_backwards(self):
        current = manifest([package("macos", "0.2.0"), package("windows", "0.2.0"),
                            package("android", "0.2.0", build=21, signing="development")])
        older = manifest([package("macos", "0.1.99"), package("windows", "0.1.99"),
                          package("android", "0.1.99", build=20, signing="development")])
        self.assertFalse(FEED.may_advance(current, older))
        self.assertTrue(FEED.may_advance(older, current))


if __name__ == "__main__":
    unittest.main()
