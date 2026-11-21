"""Atomically advance a public update manifest after the complete release is published."""

import argparse
import base64
import json
from pathlib import Path
import subprocess

REPOSITORY = "Cass-ette/claude-cchp-manager"
BRANCH = "update-feed"


def may_advance(previous: dict, current: dict) -> bool:
    def key(package):
        return package["platform"], package["arch"], package.get("signing", "")

    old = {key(p): p for p in previous["packages"]}
    new = {key(p): p for p in current["packages"]}
    for identity, package in old.items():
        candidate = new.get(identity)
        if candidate is None:
            raise ValueError(f"Refusing to remove an existing installer: {identity}")
        if tuple(map(int, candidate["version"].split("."))) < tuple(map(int, package["version"].split("."))):
            return False
        if candidate.get("build", 0) < package.get("build", 0):
            return False
    return True


def validate_complete(manifest: dict) -> None:
    if manifest.get("schema") != 1 or not manifest.get("notes"):
        raise ValueError("Invalid update manifest")
    packages = manifest.get("packages", [])
    if {p.get("platform") for p in packages} != {"macos", "windows", "android"}:
        raise ValueError("Expected a complete tri-platform manifest")
    identities = [(p.get("platform"), p.get("arch"), p.get("signing", "")) for p in packages]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate installer identity in manifest")
    for package in packages:
        version = package.get("version", "")
        if len(version.split(".")) != 3 or not all(part.isdigit() for part in version.split(".")):
            raise ValueError("Installer versions must be numeric MAJOR.MINOR.PATCH")
        if package.get("channel") != ("preview" if version.split(".")[0] == "0" else "stable"):
            raise ValueError("Installer maturity must match its version number")


def api(endpoint: str, method: str = "GET", body: dict | None = None, missing_ok: bool = False):
    command = ["gh", "api", f"repos/{REPOSITORY}/{endpoint}", "--method", method]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True)
    if result.returncode:
        if missing_ok and "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def publish(path: Path) -> None:
    contents = path.read_text(encoding="utf-8")
    manifest = json.loads(contents)
    validate_complete(manifest)
    ref = api(f"git/ref/heads/{BRANCH}", missing_ok=True)
    parent = ref["object"]["sha"] if ref else None
    if parent:
        previous = api(f"contents/update.json?ref={parent}")
        if not may_advance(json.loads(base64.b64decode(previous["content"])), manifest):
            print("A newer build is already in the update feed; leaving it in place.")
            return
    tree = api("git/trees", "POST", {"tree": [
        {"path": "update.json", "mode": "100644", "type": "blob", "content": contents},
    ]})
    commit = api("git/commits", "POST", {
        "message": "Publish complete workbench update manifest",
        "tree": tree["sha"], "parents": [parent] if parent else [],
    })
    if parent:
        api(f"git/refs/heads/{BRANCH}", "PATCH", {"sha": commit["sha"], "force": False})
    else:
        api("git/refs", "POST", {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    print("Published fixed update feed: " + commit["sha"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    publish(parser.parse_args().manifest)
