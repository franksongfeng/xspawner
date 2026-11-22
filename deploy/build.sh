#!/bin/bash
# 构建所有集群共用的 xspawner 镜像。
#
# 用法:
#   ./deploy/build.sh
#   XSPAWNER_IMAGE=xspawner:latest ./deploy/build.sh
#
# 国内网络可选用：
#   APT_MIRROR=https://mirrors.aliyun.com/ubuntu \
#   PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
#   ./deploy/build.sh
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="${XSPAWNER_IMAGE:-xspawner:latest}"

args=()
if [ -n "${APT_MIRROR:-}" ]; then
    args+=(--build-arg "APT_MIRROR=${APT_MIRROR}")
fi
if [ -n "${PIP_INDEX_URL:-}" ]; then
    args+=(--build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}")
fi

if ! docker image inspect ubuntu:20.04 >/dev/null 2>&1; then
    echo "base image ubuntu:20.04 not found, pulling..."
    if ! docker pull ubuntu:20.04; then
        echo "failed to pull ubuntu:20.04." >&2
        echo "Configure a Docker registry mirror, or pull a mirror image and tag it as ubuntu:20.04." >&2
        echo "See the '国内网络 / Docker Hub 不可达' section in DOCKER.md." >&2
        exit 1
    fi
fi

# 不强制 --pull：Docker Hub 不可达时，可先把镜像源里的 ubuntu:20.04
# 拉下来并 tag 成 ubuntu:20.04，再直接使用本地基础镜像构建。
docker build "${args[@]}" -t "${IMAGE}" -f Dockerfile .
echo "built ${IMAGE}"
