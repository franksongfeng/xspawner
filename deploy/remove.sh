#!/bin/bash
# 删除一个 xspawner 集群。
#
# 默认只删除容器和 Caddy 站点，保留数据卷和插件卷。
# 加 --purge 才会删除命名卷（不可恢复，请先备份）。
#
# 用法:
#   ./deploy/remove.sh <cluster>
#   ./deploy/remove.sh <cluster> --purge
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <cluster> [--purge]" >&2
    exit 1
fi

CLUSTER_RAW="$1"
shift

PURGE=false
for arg in "$@"; do
    case "${arg}" in
        --purge) PURGE=true ;;
        *)
            echo "unknown option: ${arg}" >&2
            echo "usage: $0 <cluster> [--purge]" >&2
            exit 1
            ;;
    esac
done

cd "$(dirname "$0")/.."

SLUG=$(printf '%s' "${CLUSTER_RAW}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9_.-]+/-/g; s/^[-_.]+//; s/[-_.]+$//')
if [ -z "${SLUG}" ]; then
    echo "invalid cluster name: ${CLUSTER_RAW}" >&2
    exit 1
fi

if [ "${PURGE}" = "true" ]; then
    echo "WARNING: --purge will delete data, plugins and certs volumes" >&2
fi

NAME="xspawner-${SLUG}-admin"
SITE="deploy/caddy-sites/${SLUG}.caddy"

if docker inspect "${NAME}" >/dev/null 2>&1; then
    # 先按容器 stop signal 正常停止 systemd，再强制移除，避免直接 kill。
    docker stop -t 30 "${NAME}" >/dev/null 2>&1 || true
    docker rm -fv "${NAME}" >/dev/null
    echo "removed container ${NAME}"
else
    echo "container ${NAME} not found"
fi

if [ -f "${SITE}" ]; then
    rm -f "${SITE}"
    echo "removed site ${SITE}"
fi

# Caddy reload 会丢弃动态路由；重启剩余集群让它们重新注册。
if docker inspect -f '{{.State.Running}}' xspawner-caddy 2>/dev/null | grep -q true; then
    ./deploy/sync-caddy.sh
fi

if [ "${PURGE}" = "true" ]; then
    for suffix in data plugins certs; do
        volume="xspawner-${SLUG}-${suffix}"
        if docker volume inspect "${volume}" >/dev/null 2>&1; then
            docker volume rm "${volume}" >/dev/null
            echo "removed volume ${volume}"
        fi
    done
else
    echo "volumes retained:"
    echo "  xspawner-${SLUG}-data"
    echo "  xspawner-${SLUG}-plugins"
    echo "use --purge to delete them"
fi

echo "cluster ${SLUG} removed"
