"""Per-repo embed state stored in a dedicated Chroma collection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

# Chroma requires names to start with [a-zA-Z0-9]; leading underscore is invalid.
STATE_COLLECTION = "grepsense_state"
PLACEHOLDER_EMBEDDING = [0.0]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_state_collection(client: Any):
    return client.get_or_create_collection(
        name=STATE_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def get_state_record(collection: Any, repo: str) -> dict[str, Any] | None:
    try:
        result = collection.get(ids=[repo], include=["metadatas"])
    except Exception:
        return None
    ids = result.get("ids") or []
    if not ids:
        return None
    meta = (result.get("metadatas") or [None])[0]
    if not meta:
        return None
    record: dict[str, Any] = {
        "watermark": meta.get("watermark"),
        "head": meta.get("head"),
    }
    if meta.get("last_run"):
        try:
            record["last_run"] = json.loads(meta["last_run"])
        except (TypeError, json.JSONDecodeError):
            record["last_run"] = meta["last_run"]
    return record


def upsert_state(
    collection: Any,
    repo: str,
    *,
    watermark: str,
    head: str | None,
    last_run: dict[str, Any],
) -> None:
    metadata = {
        "watermark": watermark,
        "head": head or "",
        "last_run": json.dumps(last_run),
    }
    collection.upsert(
        ids=[repo],
        documents=[repo],
        metadatas=[metadata],
        embeddings=[PLACEHOLDER_EMBEDDING],
    )


def delete_state_collection(client: Any) -> None:
    try:
        client.delete_collection(STATE_COLLECTION)
    except Exception:
        pass


def list_state_records(collection: Any) -> dict[str, dict[str, Any]]:
    try:
        result = collection.get(include=["metadatas"])
    except Exception:
        return {}
    records: dict[str, dict[str, Any]] = {}
    for repo_id, meta in zip(result.get("ids") or [], result.get("metadatas") or []):
        if not meta:
            continue
        record: dict[str, Any] = {
            "watermark": meta.get("watermark"),
            "head": meta.get("head"),
        }
        if meta.get("last_run"):
            try:
                record["last_run"] = json.loads(meta["last_run"])
            except (TypeError, json.JSONDecodeError):
                record["last_run"] = meta["last_run"]
        records[repo_id] = record
    return records
