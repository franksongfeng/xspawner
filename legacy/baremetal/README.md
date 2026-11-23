# Bare-metal / systemd deployment scripts

These scripts are the legacy host-level entry points for running xspawner
directly on a Linux server with systemd. They are **not used by the container
deployment**.

For containerized clusters use:

```bash
./deploy/cluster.sh <cluster> <host> <root-port>
./deploy/backup.sh <cluster> [output-dir]
./deploy/remove.sh  <cluster> [--purge]
```

## Commands

```bash
chmod +x legacy/baremetal/*

legacy/baremetal/init             # asks for a cluster name, writes <cluster>.json
legacy/baremetal/start example-com
legacy/baremetal/stop example-com
legacy/baremetal/backup example-com
legacy/baremetal/drop example-com
```

Each cluster uses its own config file:

```text
legacy/baremetal/<cluster>.json
```

For example, `example-com.json`. Multiple cluster configs can coexist in this
directory. `_common.sh` is the shared helper that resolves and validates the
config path. All scripts `cd` to the repository root, so they can be invoked
from any directory.

The scripts export `XSPAWNER_CLUSTER=<cluster>`, so the SQLite database is named
`<cluster>.db` and the log file is named `<cluster>.log` (under the repository
root, or under `XSPAWNER_DATA_DIR` if set). When upgrading an existing
deployment, rename the old `xspawner.db` / `xspawner.log` to `<cluster>.db` /
`<cluster>.log` if you want to keep the existing data and history.

An `example-com.json` sample is included. The former root-level `init`, `start`,
`stop`, `backup`, `drop` shims and the root `config.json` sample have been
removed.

## Caveats

- systemd unit names are now just the service id, e.g. `admin.service`, not
  `<host>-<id>.service`. Remove old host-prefixed units once before switching:
  `systemctl disable --now <old-unit>` and delete the old unit file.
- Because the service id becomes the unit name, avoid ids that collide with
  existing systemd units (for example `dbus`, `systemd-*`).
- If you run multiple cluster configs on the same host systemd, give each one a
  unique `id` (for example `xspawner-com` and `lepli-cn`); otherwise their
  `admin.service` units collide.
- `stop` is not just "stop": it stops the unit, disables it, and deletes the
  unit file. On bare metal, use `systemctl stop <unit>` if you only want a
  temporary stop.
- `drop` is destructive: it stops child services, removes the unit, and deletes
  the database after taking a backup. It does not delete containers or Docker
  volumes because it is not a container command.
- These scripts require the host Python environment (or an installed
  `xspawner` package) and host systemd; they do not run inside the container.
