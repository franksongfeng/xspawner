##############################################################################
# XSpawner administrator cluster image
#
# 一个容器 = 一个 administrator 服务集群：
#   根 administrator + 它管理的全部普通服务和子 administrator。
#
# 镜像与具体域名/IP 无关：同一个 xspawner:latest 镜像部署任意数量的集群
#
# 基础系统：Ubuntu 20.04 Server，Python 使用 Ubuntu 默认的 python3 (3.8.x)。
# 容器内 PID 1 为 systemd，xspawner 仍用 systemctl 管理各服务。
#
# 构建（所有集群共用，只需构建一次）：
#   docker build -t xspawner:latest .
##############################################################################

# 可选的 Ubuntu 软件源镜像，例如 http://mirrors.aliyun.com/ubuntu
ARG APT_MIRROR=

# ── 构建阶段：编译并安装 Python 依赖 ───────────────────────────────────────
FROM ubuntu:20.04 AS builder

ARG APT_MIRROR
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PYTHONUNBUFFERED=1

RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g; s|http://security.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        libcurl4-openssl-dev \
        libssl-dev \
        python3 \
        python3-dev \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 20.04 默认 Python 3.8。pip 24.3.1 是最后支持 Python 3.8 的版本。
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade \
        "pip==24.3.1" "setuptools<76" wheel \
    && /opt/venv/bin/pip install --no-cache-dir tomli

COPY pyproject.toml /tmp/pyproject.toml
COPY docker/install-deps.py /tmp/install-deps.py
RUN /opt/venv/bin/python /tmp/install-deps.py /tmp/pyproject.toml \
    && /opt/venv/bin/python -c "import tornado, pywebio, pycurl, requests, psutil, tortoise, asyncpg, aiomysql; print('xspawner dependencies ok')"

# ── 运行阶段：Ubuntu 20.04 + systemd + 系统 Python 3.8 ─────────────────────
FROM ubuntu:20.04

ARG APT_MIRROR

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    container=docker \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    XSPAWNER_ACCESS=0.0.0.0 \
    XSPAWNER_DATA_DIR=/data \
    XSPAWNER_SEVERITY=info \
    PYTHONPATH=/opt/xspawner \
    PATH="/opt/venv/bin:${PATH}"

RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g; s|http://security.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dbus \
        libcurl4 \
        procps \
        python3 \
        sqlite3 \
        systemd \
        systemd-sysv \
        tzdata \
    && ln -fs "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && systemd-machine-id-setup \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /opt/xspawner

# 代码固定在镜像里；插件目录和数据目录由命名卷覆盖，实现持久化。
COPY xspawner ./xspawner
COPY docker/entrypoint.sh /usr/local/bin/xspawner-entrypoint

RUN sed -i 's/\r$//' /usr/local/bin/xspawner-entrypoint \
    && chmod +x /usr/local/bin/xspawner-entrypoint \
    && mkdir -p "${XSPAWNER_DATA_DIR}" /opt/xspawner/certs \
    && mkdir -p /etc/systemd/system.conf.d /etc/systemd/system/multi-user.target.wants

VOLUME ["/data", "/opt/xspawner/xspawner/plugins"]
EXPOSE 8000
STOPSIGNAL SIGRTMIN+3

ENTRYPOINT ["/usr/local/bin/xspawner-entrypoint"]
CMD []
