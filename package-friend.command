#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_SOURCE="$PROJECT_DIR/build/Agent Workbench.app"
ZIP_PATH="${1:-$PROJECT_DIR/build/Agent-Workbench-macOS-universal-$(date +%Y%m%d-%H%M%S).zip}"

if (( $# > 1 )); then
    print -u2 '用法：./package-friend.command [输出 ZIP 路径]'
    exit 1
fi

if [[ ! -d "$APP_SOURCE" ]]; then
    print -u2 "未找到已构建的工作台应用：$APP_SOURCE"
    print -u2 "请先运行 ./build-app.command"
    exit 1
fi

if [[ -e "$ZIP_PATH" || -L "$ZIP_PATH" ]]; then
    print -u2 "输出路径已存在：$ZIP_PATH"
    exit 1
fi

ditto -c -k --norsrc --keepParent "$APP_SOURCE" "$ZIP_PATH"
print "打包完成：$ZIP_PATH"
