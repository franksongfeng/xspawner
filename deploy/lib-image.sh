#!/bin/bash
# 镜像仓库公共函数，由 deploy/push.sh、deploy/pull.sh 以 source 方式引入。
#
# 仓库引用统一写作：
#   [<registry>/]<namespace>/<repo>[:<tag>][@sha256:<digest>]
#
# - 没写 registry 时按 Docker Hub（docker.io）处理；
# - 仓库名必须全小写，且必须带命名空间（拒绝 `xspawner` 这种会被展开成
#   docker.io/library/xspawner 的写法），避免误推/误拉官方镜像；
# - 用户名密码只从环境变量或文件读取，不写进命令行参数，避免落进 shell 历史。

# shellcheck shell=bash

XSPAWNER_DEFAULT_REGISTRY="docker.io"
XSPAWNER_DEFAULT_IMAGE="xspawner:latest"
XSPAWNER_NO_LOGIN="${XSPAWNER_NO_LOGIN:-false}"

img::die() {
    echo "error: $*" >&2
    exit 1
}

img::warn() {
    echo "warning: $*" >&2
}

img::require_docker() {
    command -v docker >/dev/null 2>&1 || img::die "未找到 docker 命令，请先安装 Docker Engine 20.10+"
}

# 解析引用，写入 IMG_REPO（不含标签/摘要）、IMG_TAG、IMG_DIGEST。
img::parse_ref() {
    local ref="$1"
    IMG_REPO="$ref"
    IMG_TAG=""
    IMG_DIGEST=""

    case "$ref" in
        *://*)
            img::die "镜像引用不要带协议头: $ref"
            ;;
        *@sha256:*)
            IMG_DIGEST="${ref##*@}"
            IMG_REPO="${ref%@*}"
            ;;
    esac

    # 标签只看最后一个路径段里的冒号，registry 的 host:port 不受影响。
    local last="${IMG_REPO##*/}"
    case "$last" in
        *:*)
            IMG_TAG="${last##*:}"
            IMG_REPO="${IMG_REPO%:*}"
            ;;
    esac
}

# 拆分 registry host 与仓库路径，写入 IMG_HOST、IMG_PATH。
img::split_host() {
    local repo="$1"
    local first="${repo%%/*}"

    if [ "$first" != "$repo" ] && { [ "$first" = "localhost" ] || [[ "$first" == *.* || "$first" == *:* ]]; }; then
        IMG_HOST="$first"
        IMG_PATH="${repo#*/}"
    else
        IMG_HOST="$XSPAWNER_DEFAULT_REGISTRY"
        IMG_PATH="$repo"
    fi
}

# 仓库名（不含标签）是否合法。
img::valid_repo() {
    local repo="$1"
    local host path

    [ -n "$repo" ] || return 1
    case "$repo" in
        *[A-Z]*) return 1 ;;
        /*|*/|*//*) return 1 ;;
    esac

    host="${repo%%/*}"
    if [ "$host" != "$repo" ] && { [ "$host" = "localhost" ] || [[ "$host" == *.* || "$host" == *:* ]]; }; then
        [[ "$host" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?$ ]] || return 1
        path="${repo#*/}"
    else
        path="$repo"
    fi

    [[ "$path" =~ ^[a-z0-9]+([._-][a-z0-9]+)*([/][a-z0-9]+([._-][a-z0-9]+)*)*$ ]]
}

img::valid_tag() {
    [[ "$1" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]]
}

img::valid_digest() {
    [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]
}

# 从命令输出里取 digest（docker push 输出 `digest: sha256:...`，
# docker pull 输出 `Digest: sha256:...`，因此忽略大小写）。
img::digest_from_log() {
    grep -oiE 'digest: sha256:[0-9a-f]{64}' "$1" 2>/dev/null | tail -n 1 | awk '{print $2}'
}

# 登录 registry。凭据优先取 XSPAWNER_REGISTRY_PASSWORD，其次取
# XSPAWNER_REGISTRY_PASSWORD_FILE（CI 里挂载 secret 文件时用）。
# 两者都没有时不做登录，交给本机已有的 docker 凭据（~/.docker/config.json）。
img::login() {
    local server="$1"
    local password="${XSPAWNER_REGISTRY_PASSWORD:-}"

    if [ "${XSPAWNER_NO_LOGIN}" = "true" ]; then
        echo "skip docker login ${server} (XSPAWNER_NO_LOGIN=true)"
        return 0
    fi

    if [ -z "${XSPAWNER_REGISTRY_USER:-}" ]; then
        if [ -n "${password}" ]; then
            img::warn "设置了 XSPAWNER_REGISTRY_PASSWORD 但没有 XSPAWNER_REGISTRY_USER，跳过自动登录"
        fi
        echo "docker login ${server}: 未提供凭据，使用本机已有登录状态"
        return 0
    fi

    if [ -z "${password}" ] && [ -n "${XSPAWNER_REGISTRY_PASSWORD_FILE:-}" ]; then
        [ -r "${XSPAWNER_REGISTRY_PASSWORD_FILE}" ] \
            || img::die "无法读取 XSPAWNER_REGISTRY_PASSWORD_FILE: ${XSPAWNER_REGISTRY_PASSWORD_FILE}"
        password=$(<"${XSPAWNER_REGISTRY_PASSWORD_FILE}")
        # 去掉 Windows/编辑器可能留下的 CRLF 和末尾换行。
        password="${password%$'\r'}"
        password="${password%$'\n'}"
        password="${password%$'\r'}"
    fi

    [ -n "${password}" ] \
        || img::die "已设置 XSPAWNER_REGISTRY_USER，但密码为空：请设置 XSPAWNER_REGISTRY_PASSWORD 或 XSPAWNER_REGISTRY_PASSWORD_FILE"

    printf '%s' "${password}" | docker login "${server}" \
        --username "${XSPAWNER_REGISTRY_USER}" --password-stdin
}

# 校验并规范化一个“推送/拉取目标仓库”，结果写回 REPO / IMG_HOST / IMG_PATH，
# 并要求带命名空间（至少 `namespace/repo`）。
# $2=allow_digest：为 true 时允许引用带 @sha256:...（拉取按摘要固定版本）。
img::resolve_repo() {
    local repo="$1"
    local allow_digest="${2:-false}"

    img::parse_ref "${repo}"
    if [ -n "${IMG_DIGEST}" ] && [ "${allow_digest}" != "true" ]; then
        img::die "目标仓库不能带摘要: ${repo}"
    fi
    REPO="${IMG_REPO}"

    img::split_host "${REPO}"
    img::valid_repo "${REPO}" \
        || img::die "非法仓库地址: ${REPO}（要求全小写，只允许 a-z0-9._- 与 /）"

    case "${IMG_PATH}" in
        */*) ;;
        *) img::die "仓库地址需要命名空间，例如 registry.example.com/my-team/xspawner" ;;
    esac
}
