#!/bin/bash
# 重新加载 Caddy 站点配置，并重启所有 xspawner 集群容器。
#
# Caddy reload 会丢弃通过 Admin API 注入的动态路由；集群容器重启后，
# 容器内 systemd 会重新启动 administrator 及其子服务，它们启动时会重新
# 调用 Caddy Admin API 注册 /services/<id>/* 路由。
set -euo pipefail

cd "$(dirname "$0")/.."

if ! docker inspect -f '{{.State.Running}}' xspawner-caddy 2>/dev/null | grep -q true; then
    echo "xspawner-caddy 未运行；先执行: ./deploy/caddy.sh" >&2
    exit 1
fi

echo "[caddy] reloading Caddyfile"
ok=0
for _ in $(seq 1 30); do
    if docker exec xspawner-caddy \
        caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
        ok=1
        break
    fi
    sleep 1
done
if [ "${ok}" != "1" ]; then
    echo "Caddy reload failed" >&2
    exit 1
fi

ids=$(docker ps -q --filter label=xspawner.managed=true || true)
if [ -n "${ids}" ]; then
    echo "[caddy] restarting cluster containers to re-register routes"
    # shellcheck disable=SC2086
    docker restart -t 30 ${ids} >/dev/null
fi

echo "[caddy] done"
