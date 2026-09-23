"""One mutation at a time. A lock file in the plugin state dir guards new/add/drop/remove
so a CLI-invoked action cannot race the popup. Stale locks (older than 10 minutes, or
whose owner pid is gone) are taken over."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from .ui import Abort

STALE_AFTER_SECONDS = 600


def state_dir() -> Path:
    base = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if not base:
        base = Path.home() / ".local/state/herdr/plugins/feature"
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _owner_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def mutation_lock():
    path = state_dir() / "lock"
    for _attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            stale = False
            try:
                age = time.time() - path.stat().st_mtime
                owner = int(path.read_text().strip() or "0")
                stale = (not _owner_alive(owner)) if owner else age > STALE_AFTER_SECONDS
            except (OSError, ValueError):
                stale = True
            if stale:
                path.unlink(missing_ok=True)
                continue
            raise Abort(
                "Another feature command is still running. Wait for it to finish, or delete\n"
                f"{path} if you are sure it is stale."
            )
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        try:
            yield
        finally:
            path.unlink(missing_ok=True)
        return
    raise Abort(f"could not acquire {path}.")
