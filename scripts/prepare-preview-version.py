"""Give each published CI build increasing, installer-compatible platform versions."""

import argparse
import json
from pathlib import Path
import plistlib
import re


def bumped(version: str, sequence: int) -> str:
    major, minor, patch = map(int, version.split("."))
    return f"{major}.{minor}.{patch + sequence}"


def prepare(root: Path, sequence: int, stable_version: str = "") -> dict:
    if sequence < 1:
        raise ValueError("Preview sequence must be positive")
    if stable_version and not re.fullmatch(r"[1-9][0-9]*\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", stable_version):
        raise ValueError("Stable version must be numeric MAJOR.MINOR.PATCH with major >= 1")
    plist = root / "Resources/Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info["CFBundleShortVersionString"] = stable_version or bumped(info["CFBundleShortVersionString"], sequence)
    info["CFBundleVersion"] = str(int(info["CFBundleVersion"]) + sequence)
    plist.write_bytes(plistlib.dumps(info, sort_keys=False))

    frontend = root / "windows/package.json"
    version = stable_version or bumped(json.loads(frontend.read_text())["version"], sequence)
    for name in ("package.json", "package-lock.json", "src-tauri/tauri.conf.json"):
        path = root / "windows" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = version
        if name == "package-lock.json":
            data["packages"][""]["version"] = version
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, pattern in (
        ("Cargo.toml", r'(\[package\]\s+name = "windows"\s+version = ")[^"]+("{1})'),
        ("Cargo.lock", r'(\[\[package\]\]\s+name = "windows"\s+version = ")[^"]+("{1})'),
    ):
        path = root / "windows/src-tauri" / name
        text, count = re.subn(pattern, lambda m: m[1] + version + m[2], path.read_text(), count=1)
        if count != 1:
            raise ValueError(f"Cannot stamp {name}")
        path.write_text(text)

    gradle = root / "android/app/build.gradle.kts"
    text = gradle.read_text()
    android = stable_version or bumped(re.search(r'versionName = "([^"]+)"', text)[1], sequence)
    build = int(re.search(r'versionCode = ([0-9]+)', text)[1]) + sequence
    text = re.sub(r'versionName = "[^"]+"', f'versionName = "{android}"', text, count=1)
    text = re.sub(r'versionCode = [0-9]+', f'versionCode = {build}', text, count=1)
    gradle.write_text(text)
    return {"macos": info["CFBundleShortVersionString"], "windows": version, "android": android, "android_build": build}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--stable-version", default="", help="Explicit stable version; build numbers still increase with sequence")
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1], args.sequence, args.stable_version)))
