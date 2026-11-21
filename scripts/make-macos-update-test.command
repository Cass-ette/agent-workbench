#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")/.."
repo="$(pwd -P)"
test_root="${1:-$repo/build/mac-update-test}"
if [[ "$test_root" != /* ]]; then
    test_root="$repo/$test_root"
fi
if [[ -e "$test_root" || -L "$test_root" ]]; then
    print -u2 "测试目录已存在：$test_root"
    exit 1
fi

scratch="$repo/.build/local-update-test"
swift build --scratch-path "$scratch" -Xswiftc -DWORKBENCH_LOCAL_UPDATE_TEST
binary="$(swift build --scratch-path "$scratch" --show-bin-path)/ClaudeCCHPManager"
app_name="Agent 工作台 更新测试.app"
mkdir -p "$test_root/Update Feed"
test_root="$(cd "$test_root" && pwd -P)"
candidate="$(mktemp -d "$test_root/.candidate.XXXXXX")"
stage_app() {
    local app="$1" version="$2" build_number="$3"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/backend"
    cp "$binary" "$app/Contents/MacOS/ClaudeCCHPManager"
    cp Resources/Info.plist "$app/Contents/Info.plist"
    cp Resources/AppIcon.icns "$app/Contents/Resources/"
    cp backend/{session_history,usage_records,gateway_billing,model_pricing,pricing_service}.py "$app/Contents/Resources/backend/"
    cp backend/{pricing_reference,deepseek_reference}.json "$app/Contents/Resources/backend/"
    # Each test copy has its own settings and history directory alongside the app.
    sed 's@^USER_ROOT = Path.home()$@USER_ROOT = Path(__file__).resolve().parents[4] / ".test-home"@' \
        backend/claude-cchp-test.py > "$app/Contents/Resources/backend/claude-cchp-test.py"
    /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.cassette.cchpmodelmanager.updatetest" "$app/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c "Set :CFBundleName 工作台更新测试" "$app/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $version" "$app/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $build_number" "$app/Contents/Info.plist"
    codesign --force --deep --sign - "$app"
    codesign --verify --deep --strict "$app"
}

stage_app "$test_root/$app_name" 0.1.1 2
stage_app "$candidate/$app_name" 0.1.2 3
archive="$test_root/Update Feed/Agent-Workbench-macOS-0.1.2.zip"
ditto -c -k --norsrc --keepParent "$candidate/$app_name" "$archive"
python3 - "$archive" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

archive = Path(sys.argv[1])
data = {
    "schema": 1,
    "notes": "本机测试包 0.1.2。安装后工作台会重启，版本从 0.1.1 变为 0.1.2，并在同级目录保留旧版备份。",
    "packages": [{
        "platform": "macos",
        "arch": "local",
        "channel": "local-test",
        "version": "0.1.2",
        "url": archive.as_uri(),
        "size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }],
}
archive.with_name("update.json").write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
PY
print -r -- "$test_root/$app_name"
