#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
    print -u2 "未找到 Docker。请先安装并启动 Docker Desktop，再重新运行此脚本。"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    print -u2 "当前 Docker 未提供 Compose 插件，请更新 Docker Desktop。"
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    print -u2 "Docker Desktop 尚未启动。请先打开 Docker Desktop，等待其就绪后重试。"
    exit 1
fi

if [[ ! -e .env ]]; then
    umask 077
    cp .env.example .env
    for name in POSTGRES_PASSWORD CCHP_DATABASE_RUNTIME_PASSWORD CCH_TOKEN_PEPPER CCH_MASTER_KEY DRAGONFLY_KEY_SECRET; do
        value="$(openssl rand -hex 32)"
        /usr/bin/sed -i '' "s|^${name}=.*$|${name}=${value}|" .env
    done
    chmod 600 .env
else
    print "发现已有 .env，保留现有安装密钥。"
fi

print "正在下载 CCHP 容器镜像并启动本机服务……"
docker compose pull
docker compose up -d

print "等待 CCHP 就绪……"
for attempt in {1..60}; do
    if curl --silent --fail http://127.0.0.1:8080/readyz >/dev/null \
        && curl --silent --fail http://127.0.0.1:8081/readyz >/dev/null; then
        print "CCHP 已启动。"
        print "管理控制台：http://localhost:8080"
        print "模型网关：http://127.0.0.1:8081/v1"
        print "接下来在控制台添加你自己的上游供应商 Key，并创建下游调用 Key。"
        open http://localhost:8080
        exit 0
    fi
    sleep 2
done

print -u2 "服务未在两分钟内就绪。请运行 docker compose ps --all 和 docker compose logs --tail=100 cchp 检查。"
exit 1
