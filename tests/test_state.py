from __future__ import annotations

import chromadb

from grepsense.state import (
    STATE_COLLECTION,
    get_state_collection,
    get_state_record,
    list_state_records,
    upsert_state,
    delete_state_collection,
)


def test_state_round_trip(chroma_client: chromadb.EphemeralClient) -> None:
    collection = get_state_collection(chroma_client)
    upsert_state(
        collection,
        "myrepo",
        watermark="2026-06-10T12:00:00+00:00",
        head="abc123",
        last_run={"scope": "baseline", "chunks_added": 10},
    )

    record = get_state_record(collection, "myrepo")
    assert record is not None
    assert record["watermark"] == "2026-06-10T12:00:00+00:00"
    assert record["head"] == "abc123"
    assert record["last_run"]["scope"] == "baseline"

    upsert_state(
        collection,
        "other",
        watermark="2026-06-10T13:00:00+00:00",
        head="def456",
        last_run={"scope": "incremental"},
    )
    assert get_state_record(collection, "myrepo")["head"] == "abc123"
    assert len(list_state_records(collection)) == 2


def test_reset_clears_state_collection(chroma_client: chromadb.EphemeralClient) -> None:
    collection = get_state_collection(chroma_client)
    upsert_state(
        collection,
        "repo",
        watermark="t",
        head="h",
        last_run={"scope": "baseline"},
    )
    delete_state_collection(chroma_client)
    recreated = get_state_collection(chroma_client)
    assert get_state_record(recreated, "repo") is None
    assert recreated.name == STATE_COLLECTION
