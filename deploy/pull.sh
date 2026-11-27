#!/bin/bash
# 从镜像仓库拉取 xspawner 镜像，默认重命名为本地部署用的镜像名。
#
# 用法:
#   ./deploy/pull.sh <repo[:tag]>
#   ./deploy/pull.sh <repo@sha256:...>
#   XSPAWNER_REPO=<repo> XSPAWNER_TAG=1.2.3 ./deploy/pull.sh
#
# 例:
#   ./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3
#   ./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner@sha256:<64位>
#   XSPAWNER_IMAGE=xspawner:1.2.3 ./deploy/pull.sh docker.io/my-name/xspawner:1.2.3
#
# 环境变量:
#   XSPAWNER_IMAGE                 拉取后重命名的本地镜像，默认 xspawner:latest
#   XSPAWNER_REPO                  仓库地址（等价于第一个参数）
#   XSPAWNER_TAG                   引用里没写标签时使用的标签，默认 latest
#   XSPAWNER_REGISTRY_USER         仓库用户名，设置后自动 docker login
#   XSPAWNER_REGISTRY_PASSWORD     仓库密码或访问令牌
#   XSPAWNER_REGISTRY_PASSWORD_FILE  从文件读取密码（CI 推荐）
#   XSPAWNER_NO_LOGIN=true         跳过自动登录，使用本机已有凭据
#
# 默认会把拉下来的镜像同时打上 xspawner:latest，方便直接 ./deploy/cluster.sh；
# 不想要这个别名时加 --no-retag。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-image.sh
. "${SCRIPT_DIR}/lib-image.sh"

REF="${XSPAWNER_REPO:-}"
REF_SOURCE=""
NO_RETAG=false

usage() {
    cat <<'EOF'
用法: ./deploy/pull.sh <repo[:tag]|repo@sha256:...>
      XSPAWNER_REPO=<repo> XSPAWNER_TAG=1.2.3 ./deploy/pull.sh

从镜像仓库拉取 xspawner 镜像，默认同时打上 xspawner:latest。

参数:
  <repo>        仓库地址，形如 <registry>/<namespace>/<repo>[:<tag>][@sha256:...]
  --no-retag    只拉取，不重命名为本地镜像名
  --no-login    跳过自动 docker login，使用本机已有凭据

示例:
  ./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3
  XSPAWNER_IMAGE=xspawner:1.2.3 ./deploy/pull.sh docker.io/my-name/xspawner:1.2.3
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-retag) NO_RETAG=true ;;
        --no-login) XSPAWNER_NO_LOGIN=true ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            echo "error: 未知选项: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [ "${REF_SOURCE}" = "arg" ]; then
                echo "error: 只能指定一个镜像引用，多余的参数: $1" >&2
                exit 1
            fi
            if [ -n "${REF}" ]; then
                echo "error: 已通过 XSPAWNER_REPO 指定镜像引用，标签请改用 XSPAWNER_TAG" >&2
                exit 1
            fi
            REF="$1"
            REF_SOURCE="arg"
            ;;
    esac
    shift
done

if [ -z "${REF}" ]; then
    echo "error: 缺少镜像引用" >&2
    echo "提示: 也可以用 XSPAWNER_REPO 环境变量指定" >&2
    usage >&2
    exit 1
fi

img::require_docker
img::resolve_repo "${REF}" true

TAG="${IMG_TAG}"
DIGEST="${IMG_DIGEST}"

if [ -n "${DIGEST}" ]; then
    img::valid_digest "${DIGEST}" || img::die "非法摘要: ${DIGEST}（应为 sha256: 加 64 位小写十六进制）"
    PULL_REF="${REPO}@${DIGEST}"
else
    TAG="${TAG:-${XSPAWNER_TAG:-latest}}"
    img::valid_tag "${TAG}" || img::die "非法标签: ${TAG}"
    PULL_REF="${REPO}:${TAG}"
fi

echo "registry   : ${IMG_HOST}"
echo "reference  : ${PULL_REF}"

img::login "${IMG_HOST}"

pull_failed_hint() {
    cat >&2 <<EOF

拉取失败，常见原因：
  - 未登录或凭据无效：设置 XSPAWNER_REGISTRY_USER / XSPAWNER_REGISTRY_PASSWORD，
    或先执行 docker login ${IMG_HOST}
  - 标签/摘要不存在：确认推送时用的标签，或 docker manifest inspect ${PULL_REF}
  - 私有仓库走 HTTP：把 ${IMG_HOST} 加入 /etc/docker/daemon.json 的
    insecure-registries 后重启 docker
  - 网络不可达或需要代理：检查 docker daemon 的代理与 registry-mirrors 配置
EOF
}

echo "==> pull ${PULL_REF}"
log="$(mktemp)"
if ! docker pull "${PULL_REF}" 2>&1 | tee "${log}"; then
    rm -f "${log}"
    pull_failed_hint
    exit 1
fi
pulled_digest="$(img::digest_from_log "${log}" || true)"
rm -f "${log}"

if [ -n "${DIGEST}" ] && [ -n "${pulled_digest}" ] && [ "${DIGEST}" != "${pulled_digest}" ]; then
    img::die "拉取到的摘要 ${pulled_digest} 与请求的 ${DIGEST} 不一致，已中止"
fi

image_id="$(docker image inspect --format '{{.Id}}' "${PULL_REF}" 2>/dev/null || true)"

TARGET="${XSPAWNER_IMAGE:-${XSPAWNER_DEFAULT_IMAGE}}"
retagged=false
if [ "${NO_RETAG}" = "false" ]; then
    same_ref=false
    if [ "x${TARGET}" = "x${REPO}:${TAG}" ]; then
        same_ref=true
    fi
    if [ "${same_ref}" = "false" ]; then
        docker tag "${PULL_REF}" "${TARGET}"
        retagged=true
    fi
fi

echo
echo "pulled ${PULL_REF}"
if [ -n "${pulled_digest}" ]; then
    echo "  digest   : ${pulled_digest}"
fi
if [ -n "${image_id}" ]; then
    echo "  image id : ${image_id}"
fi
if [ "${retagged}" = "true" ]; then
    echo "  local    : ${TARGET}"
fi

if [ "${NO_RETAG}" = "true" ]; then
    echo
    echo "未重命名；部署时请让 XSPAWNER_IMAGE 指向该引用："
    echo "  XSPAWNER_IMAGE=${PULL_REF} ./deploy/cluster.sh <cluster> <host> <root-port>"
else
    echo
    echo "接下来："
    echo "  ./deploy/cluster.sh <cluster> <host> <root-port>"
fi
