from __future__ import annotations

import threading
import time

import pytest

from grepsense.lock import embed_lock


def test_lock_blocks_second_acquirer(tmp_path, monkeypatch) -> None:
    lock_file = tmp_path / "grepsense-embed.lock"
    monkeypatch.setenv("GREPSENSE_EMBED_LOCK", str(lock_file))

    started = threading.Event()
    release = threading.Event()
    second_acquired = threading.Event()

    def holder() -> None:
        with embed_lock():
            started.set()
            release.wait(timeout=5)

    def waiter() -> None:
        started.wait(timeout=5)
        with embed_lock():
            second_acquired.set()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    t2.start()
    time.sleep(0.2)
    assert not second_acquired.is_set()
    release.set()
    t2.join(timeout=5)
    t1.join(timeout=5)
    assert second_acquired.is_set()
