"""Container-only heartbeat; no DB queries, browser or private data in healthcheck."""

import os
from pathlib import Path
from time import time

# An account may take 15 minutes with an authorized manual challenge.
MAX_AGE_SECONDS = 20 * 60


def heartbeat():
    name = os.getenv("RANEPA_WORKER_HEARTBEAT_FILE")
    if name:
        Path(name).touch()


def healthy():
    name = os.getenv("RANEPA_WORKER_HEARTBEAT_FILE")
    if not name:
        return False
    try:
        age = time() - Path(name).stat().st_mtime
    except OSError:
        return False
    return 0 <= age <= MAX_AGE_SECONDS


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
