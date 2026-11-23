#!/usr/bin/env python3
"""Install xspawner runtime dependencies from pyproject.toml.

Used by the Docker builder stage. Only the ``[project]`` runtime dependencies
are installed; optional packaging tools are not needed in the image.
"""

import subprocess
import sys

try:
    import tomllib
except ImportError:  # Python < 3.11, e.g. Ubuntu 20.04's Python 3.8
    try:
        import tomli as tomllib
    except ImportError:
        raise SystemExit(
            "tomli is required to read pyproject.toml on Python < 3.11")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pyproject.toml"
    with open(path, "rb") as f:
        project = tomllib.load(f)["project"]

    deps = project.get("dependencies", [])

    print("Python:", sys.version.split()[0], flush=True)
    print("Installing runtime dependencies:", ", ".join(deps), flush=True)
    return subprocess.call(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])


if __name__ == "__main__":
    raise SystemExit(main())
