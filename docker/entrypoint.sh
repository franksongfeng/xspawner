#!/bin/bash
# XSpawner administrator cluster container entrypoint.
#
# 一个容器 = 一个 administrator 服务集群。本脚本：
#   1. 安装可选的 XSPAWNER_LIBS；
#   2. 生成根 administrator 的 systemd unit；
#   3. 把集群级环境写入 systemd DefaultEnvironment，供子服务继承；
#   4. exec systemd，使 PID 1 为 systemd。
set -euo pipefail

: "${XSPAWNER_ID:=admin}"
: "${XSPAWNER_PLUGIN:=administrator}"
: "${XSPAWNER_HOST:=127.0.0.1}"
: "${XSPAWNER_PORT:=8000}"
: "${XSPAWNER_ACCESS:=0.0.0.0}"
: "${XSPAWNER_SEVERITY:=info}"
: "${XSPAWNER_DATA_DIR:=/data}"
: "${XSPAWNER_CLUSTER:=default}"

# ── 可选的 pip 依赖 ────────────────────────────────────────────────────────
if [ -n "${XSPAWNER_LIBS:-}" ]; then
    set -f
    pkgs=$(printf '%s' "${XSPAWNER_LIBS}" | tr ',' ' ')
    if [ -n "${pkgs}" ]; then
        echo "[entrypoint] installing libs: ${pkgs}"
        # shellcheck disable=SC2086
        /opt/venv/bin/python -m pip install \
            --no-cache-dir --disable-pip-version-check ${pkgs}
    fi
    set +f
fi

# ── 生成 administrator 启动脚本（避免手写 systemd 转义）───────────────────
args=(
    --id "${XSPAWNER_ID}"
    --plugin "${XSPAWNER_PLUGIN}"
    --host "${XSPAWNER_HOST}"
    --port "${XSPAWNER_PORT}"
    --access "${XSPAWNER_ACCESS}"
    --severity "${XSPAWNER_SEVERITY}"
)
[ -n "${XSPAWNER_PARENT:-}" ] && args+=(--parent "${XSPAWNER_PARENT}")
[ "${XSPAWNER_LOG:-false}" = "true" ] && args+=(--log)
[ "${XSPAWNER_SSL:-false}" = "true" ] && args+=(--ssl)
[ -n "${XSPAWNER_CERTFILE:-}" ] && args+=(--certfile "${XSPAWNER_CERTFILE}")
[ -n "${XSPAWNER_KEYFILE:-}" ] && args+=(--keyfile "${XSPAWNER_KEYFILE}")
[ -n "${XSPAWNER_LIBS:-}" ] && args+=(--libs "${XSPAWNER_LIBS}")
[ -n "${XSPAWNER_ADDITIONAL:-}" ] && args+=(--additional "${XSPAWNER_ADDITIONAL}")

{
    echo '#!/bin/bash'
    printf 'exec /opt/venv/bin/python -u -m xspawner'
    printf ' %q' "${args[@]}"
    echo
} > /opt/xspawner/start-unit.sh
chmod +x /opt/xspawner/start-unit.sh

# ── administrator systemd unit ─────────────────────────────────────────────
# unit 名必须与 service.py 的 unit_name 保持一致：直接使用服务 id。
UNIT_NAME="${XSPAWNER_ID}"
cat > "/etc/systemd/system/${UNIT_NAME}.service" <<EOF
[Unit]
Description=XSpawner ${XSPAWNER_ID} (${XSPAWNER_CLUSTER})
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/xspawner
ExecStart=/opt/xspawner/start-unit.sh
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf "/etc/systemd/system/${UNIT_NAME}.service" \
    "/etc/systemd/system/multi-user.target.wants/${UNIT_NAME}.service"

# 容器里不需要的设备/登录服务
for unit in \
    systemd-udevd.service \
    systemd-logind.service \
    systemd-resolved.service \
    systemd-resolved.socket \
    systemd-timesyncd.service \
    getty@tty1.service; do
    ln -sf /dev/null "/etc/systemd/system/${unit}"
done

# ── 让所有 systemd 子服务继承集群级运行参数 ───────────────────────────────
# 子 administrator 和普通服务都需要这些集群级参数；路径/数字/布尔值不含空格，
# 因此可以直接写入 DefaultEnvironment。
{
    echo '[Manager]'
    printf 'DefaultEnvironment='
    printf ' "XSPAWNER_CLUSTER=%s"' "${XSPAWNER_CLUSTER}"
    printf ' "XSPAWNER_DATA_DIR=%s"' "${XSPAWNER_DATA_DIR}"
    printf ' "PYTHONPATH=/opt/xspawner"'
    printf ' "PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"'
    printf ' "LANG=C.UTF-8"'
    printf ' "TZ=%s"' "${TZ:-Asia/Shanghai}"
    echo
} > /etc/systemd/system.conf.d/10-xspawner.conf

mkdir -p /run/systemd /run/lock

echo "[entrypoint] starting systemd for cluster ${XSPAWNER_CLUSTER}"
exec /lib/systemd/systemd --system --unit=multi-user.target
