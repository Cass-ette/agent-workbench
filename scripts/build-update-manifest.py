"""Create the shared update manifest from the packages staged for a GitHub release."""

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import zipfile
from pathlib import Path

REPO = "https://github.com/Cass-ette/claude-cchp-manager"
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def one_file(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one {pattern} in {directory}")
    return matches[0]


def build_manifest(artifacts: Path, notes: str, tag: str, output: Path, channel: str = "preview",
                   android_development_compatible: bool = False) -> dict:
    if not re.fullmatch(f"(?:workbench-v{VERSION}|workbench-preview-[0-9]+-[0-9]+)", tag):
        raise ValueError("Release tag must use workbench-vMAJOR.MINOR.PATCH or workbench-preview-RUN-ATTEMPT")
    if channel not in {"preview", "stable"}:
        raise ValueError("Unknown release channel")
    if android_development_compatible and channel != "stable":
        raise ValueError("Android migration alias requires a stable release APK")
    if not notes.strip() or len(notes.encode("utf-8")) > 64 * 1024:
        raise ValueError("Provide release notes, up to 64 KiB")

    def package(source: Path, platform: str, arch: str, version: str, suffix: str, **extra) -> dict:
        if not re.fullmatch(VERSION, version):
            raise ValueError(f"Invalid {platform} version: {version}")
        size = source.stat().st_size
        if not 0 < size <= 1_073_741_824:
            raise ValueError(f"Invalid {platform} installer size: {size}")
        name = f"Agent-Workbench-{platform}-{arch}-{version}{suffix}"
        digest = hashlib.sha256()
        with source.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        digest = digest.hexdigest()
        shutil.copyfile(source, output / name)
        # Release metadata does not determine product maturity or update eligibility.
        status = "preview" if version.split(".")[0] == "0" else "stable"
        return dict(platform=platform, arch=arch, channel=status, version=version,
                    url=f"{REPO}/releases/download/{tag}/{name}", size=size, sha256=digest, **extra)

    mac = one_file(artifacts / "macos-agent-workbench", "*.zip")
    with zipfile.ZipFile(mac) as archive:
        info = plistlib.loads(archive.read("CCHP Model Manager.app/Contents/Info.plist"))
    if info["CFBundleIdentifier"] != "com.cassette.cchpmodelmanager":
        raise ValueError("Unexpected macOS bundle identifier")
    windows_dir = artifacts / "windows-agent-workbench-installer"
    windows_version = (windows_dir / "version.txt").read_text(encoding="utf-8").strip()
    android_dir = artifacts / ("android-agent-workbench-preview-apk" if channel == "preview" else "android-agent-workbench-stable")
    metadata = json.loads((android_dir / "output-metadata.json").read_text(encoding="utf-8"))
    variant = "debug" if channel == "preview" else "release"
    if metadata["applicationId"] != "com.cassette.workbench" or metadata["variantName"] != variant:
        raise ValueError(f"Expected the {variant} APK for com.cassette.workbench")
    apk_info, = metadata["elements"]
    if apk_info["filters"] or not 0 < apk_info["versionCode"] <= 2_100_000_000:
        raise ValueError("Expected one universal APK with a valid versionCode")
    if channel == "stable":
        expected = tag.removeprefix("workbench-v")
        versions = (info["CFBundleShortVersionString"], windows_version, apk_info["versionName"])
        if not tag.startswith("workbench-v") or expected.startswith("0.") or any(v != expected for v in versions):
            raise ValueError("Stable packages must all match the stable release tag version")

    output.mkdir(parents=True, exist_ok=False)
    packages = [
        package(mac, "macos", "universal", info["CFBundleShortVersionString"], ".zip"),
        package(one_file(windows_dir, "*-setup.exe"), "windows", "x64", windows_version, "-setup.exe"),
        package(one_file(android_dir, "*.apk"), "android", "universal", apk_info["versionName"], ".apk",
                build=apk_info["versionCode"], signing="development" if variant == "debug" else "release"),
    ]
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{p['sha256']}  {p['url'].rsplit('/', 1)[-1]}\n" for p in packages), encoding="utf-8")
    if android_development_compatible:
        # Older clients select by build type, not certificate. Both entries point to
        # the same verified release APK signed with the existing persistent key.
        packages.append(dict(packages[-1], signing="development"))
    return {"schema": 1, "notes": notes.strip(), "packages": packages}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", choices=("preview", "stable"), default="preview")
    parser.add_argument("--android-development-compatible", action="store_true",
                        help="Also offer the release APK to preview installs; requires verifying the same signing certificate")
    args = parser.parse_args()
    notes = args.notes.read_text(encoding="utf-8")
    manifest = build_manifest(args.artifacts, notes, args.tag, args.output, args.channel, args.android_development_compatible)
    (args.output / "update.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
