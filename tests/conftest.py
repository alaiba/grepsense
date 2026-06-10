from __future__ import annotations

from collections.abc import Callable

import chromadb
import pytest

from grepsense.chunker import EncodeFn


@pytest.fixture
def chroma_client() -> chromadb.EphemeralClient:
    return chromadb.EphemeralClient()


@pytest.fixture
def encode_call_counter() -> tuple[EncodeFn, Callable[[], int]]:
    calls = {"count": 0}

    def encode_fn(docs: list[str]) -> list[list[float]]:
        calls["count"] += len(docs)
        return [[0.0] * 8 for _ in docs]

    return encode_fn, lambda: calls["count"]
