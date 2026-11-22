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

legacy/baremetal/init      # interactively write legacy/baremetal/config.json
legacy/baremetal/start     # create/enable/start the systemd unit
legacy/baremetal/stop      # stop and remove the systemd unit
legacy/baremetal/backup    # online SQLite backup via the running service
legacy/baremetal/drop      # destructive: stop children, remove unit, delete DB
```

All scripts `cd` to the repository root and use
`legacy/baremetal/config.json`, so they can be invoked from any directory.

The former root-level `init`, `start`, `stop`, `backup`, `drop` shims and the
root `config.json` sample have been removed. Use the scripts in this directory
and their local `config.json` instead.

## Caveats

- `stop` is not just "stop": it stops the unit, disables it, and deletes the
  unit file. On bare metal, use `systemctl stop <unit>` if you only want a
  temporary stop.
- `drop` is destructive: it stops child services, removes the unit, and deletes
  the database after taking a backup. It does not delete containers or Docker
  volumes because it is not a container command.
- These scripts require the host Python environment (or an installed
  `xspawner` package) and host systemd; they do not run inside the container.
