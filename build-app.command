#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$PROJECT_DIR/build/Agent Workbench.app"
ARM64_BINARY="$PROJECT_DIR/.build/release/AgentWorkbench"
X86_SCRATCH="/tmp/cchp-manager-x86-build"

cd "$PROJECT_DIR"
swift build -c release
swift build --triple x86_64-apple-macosx14.0 -c release --scratch-path "$X86_SCRATCH"
ARM64_BIN_PATH="$(swift build -c release --show-bin-path)"
X86_64_BIN_PATH="$(swift build --triple x86_64-apple-macosx14.0 -c release --scratch-path "$X86_SCRATCH" --show-bin-path)"
ARM64_BINARY="$ARM64_BIN_PATH/AgentWorkbench"
X86_64_BINARY="$X86_64_BIN_PATH/AgentWorkbench"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources/backend"
lipo -create "$ARM64_BINARY" "$X86_64_BINARY" -output "$APP_PATH/Contents/MacOS/AgentWorkbench"
cp Resources/Info.plist "$APP_PATH/Contents/Info.plist"
cp backend/claude-cchp-test.py backend/session_history.py backend/agent_reasoning.py "$APP_PATH/Contents/Resources/backend/"
cp backend/tool_bridge.py backend/screen_mcp.py "$APP_PATH/Contents/Resources/backend/"
cp backend/usage_records.py "$APP_PATH/Contents/Resources/backend/usage_records.py"
cp backend/gateway_billing.py "$APP_PATH/Contents/Resources/backend/gateway_billing.py"
cp backend/model_pricing.py backend/pricing_service.py backend/pricing_reference.json backend/deepseek_reference.json "$APP_PATH/Contents/Resources/backend/"
cp Resources/AppIcon.icns "$APP_PATH/Contents/Resources/AppIcon.icns"
cp shared/CATPPUCCIN-LICENSE "$APP_PATH/Contents/Resources/CATPPUCCIN-LICENSE"
chmod 755 "$APP_PATH/Contents/MacOS/AgentWorkbench"
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"
printf 'Built: %s\n' "$APP_PATH"
