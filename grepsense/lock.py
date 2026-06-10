"""Same-host file lock for embed passes (flock)."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

_LOCK_ENV = "GREPSENSE_EMBED_LOCK"


def lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "grepsense-embed.lock"
    return Path("/tmp/grepsense-embed.lock")


def child_lock_env() -> dict[str, str]:
    """Environment for child processes to inherit the same lock file path."""
    return {_LOCK_ENV: str(lock_path())}


@contextmanager
def embed_lock(*, blocking: bool = True):
    """Acquire an exclusive flock for the duration of an embed pass.

  A second runner blocks until the lock is released (same host/container only).
    """
    path = Path(os.environ.get(_LOCK_ENV, str(lock_path())))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(fd, flags)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
