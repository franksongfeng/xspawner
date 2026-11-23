#!/bin/bash
# Shared by the bare-metal entry scripts.
#
# Usage (sourced):
#   source _common.sh <cluster>
#
# Exports ROOT, CLUSTER and CONFIG. CONFIG is
# <repo>/legacy/baremetal/<cluster>.json.
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <cluster>" >&2
    exit 1
fi

CLUSTER="$1"
if [ -z "${CLUSTER}" ]; then
    echo "usage: $0 <cluster>" >&2
    exit 1
fi
if ! [[ "${CLUSTER}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "invalid cluster name: ${CLUSTER}" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="${ROOT}/legacy/baremetal/${CLUSTER}.json"

if [ ! -f "${CONFIG}" ]; then
    echo "config not found: ${CONFIG}" >&2
    echo "create it with: legacy/baremetal/init" >&2
    exit 1
fi

# The database and systemd units follow the cluster name.
export XSPAWNER_CLUSTER="${CLUSTER}"
export ROOT CLUSTER CONFIG
