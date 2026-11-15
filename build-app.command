#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$PROJECT_DIR/build/CCHP Model Manager.app"

cd "$PROJECT_DIR"
swift build -c release
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources/backend"
cp .build/release/ClaudeCCHPManager "$APP_PATH/Contents/MacOS/ClaudeCCHPManager"
cp Resources/Info.plist "$APP_PATH/Contents/Info.plist"
cp backend/claude-cchp-test.py "$APP_PATH/Contents/Resources/backend/claude-cchp-test.py"
chmod 755 "$APP_PATH/Contents/MacOS/ClaudeCCHPManager"
printf 'Built: %s\n' "$APP_PATH"
