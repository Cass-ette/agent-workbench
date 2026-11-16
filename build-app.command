#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$PROJECT_DIR/build/CCHP Model Manager.app"
ARM64_BINARY="$PROJECT_DIR/.build/release/ClaudeCCHPManager"
X86_SCRATCH="/tmp/cchp-manager-x86-build"
X86_64_BINARY="$X86_SCRATCH/out/Products/Release/ClaudeCCHPManager"

cd "$PROJECT_DIR"
swift build -c release
swift build --triple x86_64-apple-macosx14.0 -c release --scratch-path "$X86_SCRATCH"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources/backend"
lipo -create "$ARM64_BINARY" "$X86_64_BINARY" -output "$APP_PATH/Contents/MacOS/ClaudeCCHPManager"
cp Resources/Info.plist "$APP_PATH/Contents/Info.plist"
cp backend/claude-cchp-test.py "$APP_PATH/Contents/Resources/backend/claude-cchp-test.py"
chmod 755 "$APP_PATH/Contents/MacOS/ClaudeCCHPManager"
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"
printf 'Built: %s\n' "$APP_PATH"
