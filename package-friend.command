#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_SOURCE="$PROJECT_DIR/build/CCHP Model Manager.app"
STAGING_ROOT="$(mktemp -d "$PROJECT_DIR/build/friend-package.XXXXXX")"
PACKAGE_DIR="$STAGING_ROOT/CCHP Model Manager - Friend Setup"
ZIP_PATH="$PROJECT_DIR/build/CCHP Model Manager - Friend Setup-$(date +%Y%m%d-%H%M%S).zip"
trap 'rm -rf "$STAGING_ROOT"' EXIT

if [[ ! -d "$APP_SOURCE" ]]; then
    print -u2 "未找到已构建的管理器应用：$APP_SOURCE"
    print -u2 "请先运行 ./build-app.command"
    exit 1
fi

mkdir -p "$PACKAGE_DIR/CCHP Gateway Setup"
ditto "$APP_SOURCE" "$PACKAGE_DIR/CCHP Model Manager.app"
for file in compose.yml .env.example README.md setup.command stop.command; do
    cp "$PROJECT_DIR/deployment/cchp-local/$file" "$PACKAGE_DIR/CCHP Gateway Setup/$file"
done
chmod 755 "$PACKAGE_DIR/CCHP Gateway Setup/setup.command" "$PACKAGE_DIR/CCHP Gateway Setup/stop.command"
ditto -c -k --norsrc --keepParent "$PACKAGE_DIR" "$ZIP_PATH"
print "打包完成：$ZIP_PATH"
