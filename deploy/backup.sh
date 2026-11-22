#!/bin/bash
# 在线备份一个 xspawner 集群的 SQLite 数据库和插件目录。
#
# 在容器内用 sqlite3 的 .backup 命令做一致性备份（自动合并 WAL），
# 同时打包插件目录，然后把备份文件复制到宿主机。
#
# 用法:
#   ./deploy/backup.sh <cluster> [output-dir]
#
# 例:
#   ./deploy/backup.sh example-com
#   ./deploy/backup.sh example-com ./backups
#
# 环境变量:
#   XSPAWNER_DB          容器内数据库路径，默认 /data/xspawner.db
#   XSPAWNER_PLUGIN_DIR  容器内插件目录，默认 /opt/xspawner/xspawner/plugins
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <cluster> [output-dir]" >&2
    exit 1
fi

CLUSTER_RAW="$1"
OUT_DIR="${2:-$PWD/backups}"
case "${OUT_DIR}" in
    /*) ;;
    *) OUT_DIR="$PWD/${OUT_DIR}" ;;
esac

cd "$(dirname "$0")/.."

SLUG=$(printf '%s' "${CLUSTER_RAW}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9_.-]+/-/g; s/^[-_.]+//; s/[-_.]+$//')
if [ -z "${SLUG}" ]; then
    echo "invalid cluster name: ${CLUSTER_RAW}" >&2
    exit 1
fi

NAME="xspawner-${SLUG}-admin"
DB="${XSPAWNER_DB:-/data/xspawner.db}"
PLUGIN_DIR="${XSPAWNER_PLUGIN_DIR:-/opt/xspawner/xspawner/plugins}"

if ! docker inspect -f '{{.State.Running}}' "${NAME}" 2>/dev/null | grep -q true; then
    echo "container ${NAME} is not running" >&2
    exit 1
fi
if ! docker exec "${NAME}" test -f "${DB}"; then
    echo "database ${DB} not found in ${NAME}" >&2
    exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
DB_FILE="xspawner-${SLUG}-${TS}.db"
PLUGIN_FILE="xspawner-${SLUG}-${TS}-plugins.tgz"

docker exec "${NAME}" mkdir -p /data/backups
printf ".backup '/data/backups/%s'\n" "${DB_FILE}" \
    | docker exec -i "${NAME}" sqlite3 "${DB}"
docker exec "${NAME}" tar czf "/data/backups/${PLUGIN_FILE}" \
    -C "${PLUGIN_DIR}" .

mkdir -p "${OUT_DIR}"
docker cp "${NAME}:/data/backups/${DB_FILE}" "${OUT_DIR}/${DB_FILE}"
docker cp "${NAME}:/data/backups/${PLUGIN_FILE}" "${OUT_DIR}/${PLUGIN_FILE}"

echo "backup created"
echo "  database  : ${OUT_DIR}/${DB_FILE}"
echo "  plugins   : ${OUT_DIR}/${PLUGIN_FILE}"
echo "  container : ${NAME}:/data/backups/"
