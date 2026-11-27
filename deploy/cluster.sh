#!/bin/bash
# 在同一台服务器上部署一个 administrator 服务集群。
#
# 一个集群 = 一个容器 = 根 administrator + 它管理的全部服务。
# 所有集群复用同一个 xspawner 镜像，不使用 Docker Compose。
#
# 用法:
#   ./deploy/cluster.sh <cluster> <host> <root-port>
#
# 例:
#   ./deploy/cluster.sh example-com example.com 8000
#   ./deploy/cluster.sh 10-0-0-5    10.0.0.5    9000
#
# 前置条件:
#   Docker Engine 20.10+（无需 Compose）
#
# 镜像来源:
#   默认用本地 XSPAWNER_IMAGE，缺失时 ./deploy/build.sh 构建；
#   XSPAWNER_PULL=true 改为从镜像仓库拉取，XSPAWNER_PULL=always 每次部署都拉取。
set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <cluster> <host> <root-port>" >&2
    exit 1
fi

CLUSTER_RAW="$1"
HOST="$2"
ROOT_PORT="$3"

if [ -z "${HOST}" ]; then
    echo "host must not be empty" >&2
    exit 1
fi
if ! [[ "${HOST}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "invalid host (use a domain or IPv4 address, without scheme/path): ${HOST}" >&2
    exit 1
fi

is_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

if ! is_port "${ROOT_PORT}"; then
    echo "invalid root port: ${ROOT_PORT}" >&2
    exit 1
fi

cd "$(dirname "$0")/.."

SLUG=$(printf '%s' "${CLUSTER_RAW}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9_.-]+/-/g; s/^[-_.]+//; s/[-_.]+$//')
if [ -z "${SLUG}" ]; then
    echo "invalid cluster name: ${CLUSTER_RAW}" >&2
    exit 1
fi

IMAGE="${XSPAWNER_IMAGE:-xspawner:latest}"
NAME="xspawner-${SLUG}-admin"
DATA_VOLUME="xspawner-${SLUG}-data"
PLUGIN_VOLUME="xspawner-${SLUG}-plugins"
SSL="${XSPAWNER_SSL:-true}"
MANAGE_CADDY="${XSPAWNER_MANAGE_CADDY:-true}"
ACCESS="${XSPAWNER_ACCESS:-}"
if [ -z "${ACCESS}" ]; then
    if [ "${SSL}" = "true" ]; then
        # 启用 Caddy 时服务只监听回环地址，由 Caddy 统一对外。
        ACCESS=127.0.0.1
    else
        ACCESS=0.0.0.0
    fi
fi

# ── 1. 所有集群共用的镜像 ─────────────────────────────────────────────────
# 默认：本地没有 XSPAWNER_IMAGE 时用 ./deploy/build.sh 构建。
# XSPAWNER_PULL=true   本地没有时从镜像仓库拉取（XSPAWNER_IMAGE 需为仓库引用）
# XSPAWNER_PULL=always 每次部署都拉取，用于仓库里已有新镜像的滚动升级
PULL_MODE="${XSPAWNER_PULL:-false}"
case "${PULL_MODE}" in
    true|always|false) ;;
    *)
        echo "invalid XSPAWNER_PULL: ${PULL_MODE} (use true | always | false)" >&2
        exit 1
        ;;
esac

if [ "${PULL_MODE}" = "always" ] || ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    if [ "${PULL_MODE}" = "false" ]; then
        ./deploy/build.sh
    else
        case "${IMAGE}" in
            */*) ;;
            *)
                echo "XSPAWNER_PULL=${PULL_MODE} 需要把 XSPAWNER_IMAGE 设为镜像仓库引用，" >&2
                echo "例如 XSPAWNER_IMAGE=registry.example.com/my-ns/xspawner:1.2.3；当前为 ${IMAGE}" >&2
                exit 1
                ;;
        esac
        ./deploy/pull.sh --no-retag "${IMAGE}"
    fi
fi

# ── 2. Caddy（可选）：TLS 与 /services/<id>/* 动态路由 ─────────────────────
if [ "${SSL}" = "true" ] && [ "${MANAGE_CADDY}" = "true" ]; then
    ./deploy/caddy.sh

    mkdir -p deploy/caddy-sites
    cat > "deploy/caddy-sites/${SLUG}.caddy" <<EOF
https://${HOST} {
    tls internal
    handle {
        respond "Not Found" 404
    }
}
EOF

    # Caddy reload 会丢弃动态路由，重启已有集群容器让路由重新注册。
    ./deploy/sync-caddy.sh
fi

# ── 3. 持久化卷 ───────────────────────────────────────────────────────────
docker volume create "${DATA_VOLUME}" >/dev/null
docker volume create "${PLUGIN_VOLUME}" >/dev/null

# ── 4. 启动/重建集群容器 ──────────────────────────────────────────────────
docker rm -f "${NAME}" >/dev/null 2>&1 || true

env_args=(
    --env "XSPAWNER_CLUSTER=${SLUG}"
    --env "XSPAWNER_ID=admin"
    --env "XSPAWNER_PLUGIN=administrator"
    --env "XSPAWNER_HOST=${HOST}"
    --env "XSPAWNER_PORT=${ROOT_PORT}"
    --env "XSPAWNER_ACCESS=${ACCESS}"
    --env "XSPAWNER_SSL=${SSL}"
    --env "XSPAWNER_LOG=${XSPAWNER_LOG:-true}"
    --env "XSPAWNER_SEVERITY=${XSPAWNER_SEVERITY:-info}"
    --env "XSPAWNER_DATA_DIR=/data"
)
if [ -n "${XSPAWNER_LIBS:-}" ]; then
    env_args+=(--env "XSPAWNER_LIBS=${XSPAWNER_LIBS}")
fi
if [ -n "${XSPAWNER_ADDITIONAL:-}" ]; then
    env_args+=(--env "XSPAWNER_ADDITIONAL=${XSPAWNER_ADDITIONAL}")
fi
# administrator 子服务巡检参数（见 xspawner/plugins/administrator/administrator.py）
if [ -n "${XSPAWNER_CHILD_REAP_INTERVAL:-}" ]; then
    env_args+=(--env "XSPAWNER_CHILD_REAP_INTERVAL=${XSPAWNER_CHILD_REAP_INTERVAL}")
fi
if [ -n "${XSPAWNER_CHILD_DEAD_THRESHOLD:-}" ]; then
    env_args+=(--env "XSPAWNER_CHILD_DEAD_THRESHOLD=${XSPAWNER_CHILD_DEAD_THRESHOLD}")
fi
if [ -n "${XSPAWNER_CHILD_PROBE_TIMEOUT:-}" ]; then
    env_args+=(--env "XSPAWNER_CHILD_PROBE_TIMEOUT=${XSPAWNER_CHILD_PROBE_TIMEOUT}")
fi
if [ -n "${XSPAWNER_SHUTDOWN_GRACE:-}" ]; then
    env_args+=(--env "XSPAWNER_SHUTDOWN_GRACE=${XSPAWNER_SHUTDOWN_GRACE}")
fi

docker run -d \
    --name "${NAME}" \
    --label xspawner.managed=true \
    --label "xspawner.cluster=${SLUG}" \
    --label xspawner.role=root \
    --network host \
    --privileged \
    --cgroupns=host \
    --stop-signal SIGRTMIN+3 \
    --stop-timeout 30 \
    --tmpfs /run \
    --tmpfs /run/lock \
    --tmpfs /tmp \
    --restart unless-stopped \
    "${env_args[@]}" \
    --volume "${PLUGIN_VOLUME}:/opt/xspawner/xspawner/plugins" \
    --volume "${DATA_VOLUME}:/data" \
    "${IMAGE}" >/dev/null

echo "cluster ${SLUG} started"
echo "  image     : ${IMAGE}"
echo "  container : ${NAME}"
echo "  host      : ${HOST}"
echo "  root port : ${ROOT_PORT}"
echo "  access    : ${ACCESS}"
if [ "${SSL}" = "true" ] && [ "${MANAGE_CADDY}" != "true" ]; then
    echo "  caddy     : external 127.0.0.1:2019"
fi
if [ "${SSL}" = "true" ]; then
    echo "  UI        : https://${HOST}/services/admin/"
else
    # 不使用 Caddy 时服务直接监听端口，UI 在根路径 "/"，没有 /services/<id>/ 前缀。
    echo "  UI        : http://${HOST}:${ROOT_PORT}/"
fi
