#!/bin/bash
# 启动/确保共享 Caddy 容器（可选，不使用 Docker Compose）。
#
# 所有 xspawner 集群共享一个 Caddy：
#   - 监听宿主机 80/443；
#   - 通过 Admin API（127.0.0.1:2019）接收动态路由；
#   - 站点配置来自 deploy/caddy-sites/*.caddy。
set -euo pipefail

cd "$(dirname "$0")/.."

docker volume create xspawner-caddy-data >/dev/null
docker volume create xspawner-caddy-config >/dev/null

if ! docker inspect xspawner-caddy >/dev/null 2>&1; then
    docker run -d \
        --name xspawner-caddy \
        --network host \
        --restart unless-stopped \
        --volume "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
        --volume "$PWD/deploy/caddy-sites:/etc/caddy/sites:ro" \
        --volume xspawner-caddy-data:/data \
        --volume xspawner-caddy-config:/config \
        caddy:2 >/dev/null
else
    docker start xspawner-caddy >/dev/null 2>&1 || true
fi

# 等待 Caddy 进程可用；配置由 sync-caddy.sh 统一 reload。
for _ in $(seq 1 30); do
    if docker exec xspawner-caddy caddy version >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done

echo "Caddy container did not become ready" >&2
exit 1
