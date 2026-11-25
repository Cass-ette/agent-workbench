#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

port_is_free() {
    [[ "$1" =~ '^[0-9]{1,5}$' ]] || return 1
    (( $1 > 0 && $1 <= 65535 )) || return 1
    ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

pick_port() {
    local candidate="$1"
    while (( candidate <= 65535 )) && ! port_is_free "$candidate"; do
        candidate=$((candidate + 1))
    done
    if (( candidate > 65535 )); then
        print -u2 "找不到可用的本机端口。请关闭占用 8080 附近端口的服务后重试。"
        return 1
    fi
    print "$candidate"
}

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

control_port="$(grep '^CCHP_CONTROL_PORT=' .env | cut -d= -f2- || true)"
gateway_port="$(grep '^CCHP_GATEWAY_PORT=' .env | cut -d= -f2- || true)"
control_port="${control_port:-8080}"
gateway_port="${gateway_port:-8081}"
if ! curl --silent --fail "http://127.0.0.1:${control_port}/readyz" >/dev/null \
    || ! curl --silent --fail "http://127.0.0.1:${gateway_port}/readyz" >/dev/null; then
    if ! port_is_free "$control_port"; then
        control_port="$(pick_port 8080)"
    fi
    if [[ "$gateway_port" == "$control_port" ]] || ! port_is_free "$gateway_port"; then
        gateway_port="$(pick_port $((control_port + 1)))"
    fi
fi

# Host ports can move when another local service owns 8080/8081. The container
# ports remain 8080/8081, so CCHP's internal health checks and peer URLs do not change.
for name in CCHP_CONTROL_PORT CCHP_GATEWAY_PORT CCH_PUBLIC_BASE_URL CCH_GATEWAY_PUBLIC_URL; do
    if ! grep -q "^${name}=" .env; then
        print "${name}=" >> .env
    fi
done
/usr/bin/sed -i '' "s|^CCHP_CONTROL_PORT=.*$|CCHP_CONTROL_PORT=${control_port}|" .env
/usr/bin/sed -i '' "s|^CCHP_GATEWAY_PORT=.*$|CCHP_GATEWAY_PORT=${gateway_port}|" .env
/usr/bin/sed -i '' "s|^CCH_PUBLIC_BASE_URL=.*$|CCH_PUBLIC_BASE_URL=http://localhost:${control_port}|" .env
/usr/bin/sed -i '' "s|^CCH_GATEWAY_PUBLIC_URL=.*$|CCH_GATEWAY_PUBLIC_URL=http://localhost:${gateway_port}|" .env

print "本机控制台端口：${control_port}；模型网关端口：${gateway_port}"

print "正在下载 CCHP 容器镜像并启动本机服务……"
docker compose pull
docker compose up -d

print "等待 CCHP 就绪……"
for attempt in {1..60}; do
    if curl --silent --fail "http://127.0.0.1:${control_port}/readyz" >/dev/null \
        && curl --silent --fail "http://127.0.0.1:${gateway_port}/readyz" >/dev/null; then
        print "CCHP 已启动。"
        print "管理控制台：http://localhost:${control_port}"
        print "模型网关：http://127.0.0.1:${gateway_port}/v1"
        print "接下来在控制台添加你自己的上游供应商 Key，并创建下游调用 Key。"
        print "然后在 Agent 工作台的调用配置里填写 http://127.0.0.1:${gateway_port} 和那把下游 Key。"
        open "http://localhost:${control_port}"
        exit 0
    fi
    sleep 2
done

print -u2 "服务未在两分钟内就绪。请运行 docker compose ps --all 和 docker compose logs --tail=100 cchp 检查。"
exit 1
