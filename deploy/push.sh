#!/bin/bash
# 把本地 xspawner 镜像推送到指定镜像仓库。
#
# 用法:
#   ./deploy/push.sh <repo> [tag...]
#   ./deploy/push.sh <repo> --all-tags
#   XSPAWNER_REPO=<repo> XSPAWNER_TAG=1.2.3 ./deploy/push.sh
#
# 例:
#   ./deploy/push.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner 1.2.3 latest
#   ./deploy/push.sh docker.io/my-name/xspawner --all-tags
#
# 环境变量:
#   XSPAWNER_IMAGE                 本地镜像，默认 xspawner:latest
#   XSPAWNER_REPO                  仓库地址（等价于第一个参数）
#   XSPAWNER_TAG                   默认标签：不传标签参数时使用，再退回本地镜像标签
#   XSPAWNER_REGISTRY_USER         仓库用户名，设置后自动 docker login
#   XSPAWNER_REGISTRY_PASSWORD     仓库密码或访问令牌
#   XSPAWNER_REGISTRY_PASSWORD_FILE  从文件读取密码（CI 推荐）
#   XSPAWNER_NO_LOGIN=true         跳过自动登录，使用本机已有凭据
#
# 推送不会改动本地镜像名（xspawner:latest 仍然可用）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-image.sh
. "${SCRIPT_DIR}/lib-image.sh"

SOURCE="${XSPAWNER_IMAGE:-${XSPAWNER_DEFAULT_IMAGE}}"
REPO="${XSPAWNER_REPO:-}"
ALL_TAGS=false
TAGS=()

usage() {
    cat <<'EOF'
用法: ./deploy/push.sh <repo> [tag...]
      ./deploy/push.sh <repo> --all-tags
      XSPAWNER_REPO=<repo> XSPAWNER_TAG=1.2.3 ./deploy/push.sh

把本地 xspawner 镜像推送到指定镜像仓库。推送不会改动本地镜像名。

参数:
  <repo>        仓库地址，形如 <registry>/<namespace>/<repo>
  [tag...]      要推送的标签；省略时用 XSPAWNER_TAG，再省略时用本地镜像标签
  --all-tags    推送本地镜像在 <repo> 下的全部标签
  --no-login    跳过自动 docker login，使用本机已有凭据

用 XSPAWNER_REPO 指定仓库时，位置参数一律按标签处理。

示例:
  ./deploy/push.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner 1.2.3 latest
  XSPAWNER_REGISTRY_USER=me XSPAWNER_REGISTRY_PASSWORD="$TOKEN" \
      ./deploy/push.sh docker.io/me/xspawner 1.2.3
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --all-tags) ALL_TAGS=true ;;
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
            if [ -z "${REPO}" ]; then
                REPO="$1"
            else
                TAGS+=("$1")
            fi
            ;;
    esac
    shift
done

if [ -z "${REPO}" ]; then
    echo "error: 缺少镜像仓库地址" >&2
    echo "提示: 也可以用 XSPAWNER_REPO 环境变量指定" >&2
    usage >&2
    exit 1
fi

img::require_docker
img::resolve_repo "${REPO}"

# <repo> 自带标签时（registry/ns/xspawner:1.2.3）优先作为第一个待推标签。
if [ -n "${IMG_TAG}" ]; then
    TAGS=("${IMG_TAG}" ${TAGS[@]+"${TAGS[@]}"})
fi

if [ "${ALL_TAGS}" = "true" ] && [ "${#TAGS[@]}" -gt 0 ]; then
    img::die "--all-tags 不能和显式标签一起使用"
fi

if [ "${ALL_TAGS}" = "false" ] && [ "${#TAGS[@]}" -eq 0 ]; then
    default_tag="${XSPAWNER_TAG:-}"
    if [ -z "${default_tag}" ]; then
        img::parse_ref "${SOURCE}"
        default_tag="${IMG_TAG:-latest}"
    fi
    TAGS=("${default_tag}")
fi

# 去重，避免同一个标签重复 push。
if [ "${#TAGS[@]}" -gt 0 ]; then
    uniq_tags=()
    for tag in "${TAGS[@]}"; do
        img::valid_tag "${tag}" || img::die "非法标签: ${tag}（允许字母、数字、_ . -，不能以 . 或 - 开头）"
        duplicate=false
        if [ "${#uniq_tags[@]}" -gt 0 ]; then
            for seen in "${uniq_tags[@]}"; do
                if [ "${seen}" = "${tag}" ]; then
                    duplicate=true
                    break
                fi
            done
        fi
        if [ "${duplicate}" = "false" ]; then
            uniq_tags+=("${tag}")
        fi
    done
    TAGS=("${uniq_tags[@]}")
fi

docker image inspect "${SOURCE}" >/dev/null 2>&1 \
    || img::die "本地镜像 ${SOURCE} 不存在，请先运行 ./deploy/build.sh"

echo "source image : ${SOURCE}"
echo "registry     : ${IMG_HOST}"
echo "repository   : ${IMG_PATH}"

img::login "${IMG_HOST}"

push_failed_hint() {
    cat >&2 <<EOF

推送失败，常见原因：
  - 未登录或凭据无效：设置 XSPAWNER_REGISTRY_USER / XSPAWNER_REGISTRY_PASSWORD，
    或先执行 docker login ${IMG_HOST}
  - 仓库不存在或无权限：在镜像服务控制台先建好命名空间/仓库
    （例如阿里云 ACR 的 ${IMG_PATH}，Docker Hub 的 <user>/<repo>）
  - 仓库要求 HTTPS 但本机把它当 HTTP：把仓库域名加入
    /etc/docker/daemon.json 的 insecure-registries 后重启 docker
EOF
}

if [ "${ALL_TAGS}" = "true" ]; then
    echo "==> push --all-tags ${REPO}"
    log="$(mktemp)"
    if ! docker push --all-tags "${REPO}" 2>&1 | tee "${log}"; then
        rm -f "${log}"
        push_failed_hint
        exit 1
    fi
    digest="$(img::digest_from_log "${log}" || true)"
    rm -f "${log}"
    echo
    echo "pushed all tags of ${REPO}"
    if [ -n "${digest}" ]; then
        echo "  digest: ${digest}"
    fi
    exit 0
fi

pushed=()
for tag in "${TAGS[@]}"; do
    ref="${REPO}:${tag}"
    if [ "${SOURCE}" != "${ref}" ]; then
        docker tag "${SOURCE}" "${ref}"
    fi

    echo "==> push ${ref}"
    log="$(mktemp)"
    if ! docker push "${ref}" 2>&1 | tee "${log}"; then
        rm -f "${log}"
        push_failed_hint
        exit 1
    fi
    digest="$(img::digest_from_log "${log}" || true)"
    rm -f "${log}"

    pushed+=("${ref}")
    if [ -n "${digest}" ]; then
        echo "    digest: ${digest}"
    else
        echo "    digest: (未能从输出中解析)"
    fi
done

echo
echo "pushed ${#pushed[@]} tag(s):"
for ref in "${pushed[@]}"; do
    echo "  ${ref}"
done
echo
echo "在目标机器上拉取："
echo "  ./deploy/pull.sh ${pushed[0]}"
