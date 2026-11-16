#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
docker compose down
print "CCHP 已停止；数据库和密钥仍保存在本机 Docker 数据卷及 .env 中。"
