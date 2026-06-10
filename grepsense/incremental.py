"""Incremental embed orchestrator."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import chromadb

from . import semantic
from .chunker import (
    delete_chunks_for_paths,
    embed_paths,
    embed_repo,
    embed_repo_fallback_skip,
    model_encode_fn,
)
from .config import Config
from .discovery import resolve_targets
from .gitchanges import changed_paths, current_head
from .lock import embed_lock
from .state import (
    delete_state_collection,
    get_state_collection,
    get_state_record,
    list_state_records,
    upsert_state,
    utc_now_iso,
)


def _chroma_client(config: Config) -> chromadb.HttpClient:
    return chromadb.HttpClient(host=config.chroma_host, port=config.chroma_port)


def _reset_collections(client: chromadb.HttpClient, config: Config) -> None:
    try:
        client.delete_collection(config.collection)
    except Exception:
        pass
    delete_state_collection(client)


def run_once(
    config: Config,
    *,
    repo_filter: str | None = None,
    reset: bool = False,
    incremental: bool = True,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Run one embed pass (baseline, incremental, or fallback per repo)."""
    with embed_lock():
        effective_root, repos = resolve_targets(config.root)
        if not repos:
            raise RuntimeError(f"grepsense: no git repos found under {config.root}")
        if repo_filter:
            repos = [r for r in repos if r == repo_filter]
            if not repos:
                raise RuntimeError(
                    f"grepsense: repo '{repo_filter}' not found under {config.root}"
                )

        client = _chroma_client(config)
        if reset:
            _reset_collections(client, config)

        data_collection = client.get_or_create_collection(
            name=config.collection,
            metadata={"hnsw:space": "cosine"},
        )
        state_collection = get_state_collection(client)
        model = semantic.load_model(config.embedding_model)
        encode_fn = model_encode_fn(model)

        repo_results: list[dict[str, Any]] = []
        total_chunks = 0
        total_files = 0

        for name in repos:
            repo_path = effective_root / name
            if not repo_path.is_dir():
                continue

            started = utc_now_iso()
            t0 = time.monotonic()
            state = None if reset else get_state_record(state_collection, name)
            is_git = (repo_path / ".git").is_dir()
            chunks_added = 0
            chunks_deleted = 0
            files_changed = 0
            file_count = 0

            if not incremental or state is None:
                scope = "baseline"
                if is_git:
                    chunks_added, file_count = embed_repo(
                        data_collection,
                        encode_fn,
                        config,
                        name,
                        repo_path,
                        batch_size=batch_size,
                    )
                else:
                    chunks_added, file_count = embed_repo_fallback_skip(
                        data_collection,
                        encode_fn,
                        config,
                        name,
                        repo_path,
                        batch_size=batch_size,
                    )
                    scope = "fallback"
            elif is_git:
                scope = "incremental"
                changed = changed_paths(repo_path, state)
                files_changed = len(changed)
                if changed:
                    chunks_deleted = delete_chunks_for_paths(
                        data_collection, name, changed
                    )
                    existing = {p for p in changed if (repo_path / p).is_file()}
                    chunks_added = embed_paths(
                        data_collection,
                        encode_fn,
                        config,
                        name,
                        repo_path,
                        existing,
                        batch_size=batch_size,
                    )
                file_count = files_changed
            else:
                scope = "fallback"
                chunks_added, file_count = embed_repo_fallback_skip(
                    data_collection,
                    encode_fn,
                    config,
                    name,
                    repo_path,
                    batch_size=batch_size,
                )

            completed = utc_now_iso()
            duration_s = round(time.monotonic() - t0, 3)
            head = current_head(repo_path) if is_git else None
            last_run = {
                "started": started,
                "completed": completed,
                "scope": scope,
                "files_changed": files_changed,
                "chunks_added": chunks_added,
                "chunks_deleted": chunks_deleted,
                "duration_s": duration_s,
            }
            upsert_state(
                state_collection,
                name,
                watermark=completed,
                head=head,
                last_run=last_run,
            )

            total_chunks += chunks_added
            total_files += file_count
            repo_results.append({"repo": name, **last_run})
            print(
                f"  {name}: {scope} — "
                f"{chunks_added} chunks added, {chunks_deleted} deleted "
                f"({duration_s}s)"
            )

        return {
            "repos": repos,
            "repo_results": repo_results,
            "files": total_files,
            "chunks": total_chunks,
            "collection_count": data_collection.count(),
        }


def format_status(config: Config) -> str:
    """Return a human-readable status table for all repos."""
    client = _chroma_client(config)
    try:
        data_collection = client.get_collection(config.collection)
        collection_count = data_collection.count()
    except Exception:
        collection_count = 0

    state_collection = get_state_collection(client)
    records = list_state_records(state_collection)
    _, repos = resolve_targets(config.root)

    lines = [
        f"Collection '{config.collection}': {collection_count} vectors",
        "",
        f"{'REPO':<20} {'SCOPE':<12} {'COMPLETED':<22} {'FILES':>6} {'+CHUNKS':>8} {'-CHUNKS':>8} {'DUR(s)':>7}",
        "-" * 90,
    ]
    for repo in repos:
        record = records.get(repo)
        if not record:
            lines.append(f"{repo:<20} {'(no state)':<12}")
            continue
        last = record.get("last_run") or {}
        lines.append(
            f"{repo:<20} "
            f"{str(last.get('scope', '')):<12} "
            f"{str(last.get('completed', '')):<22} "
            f"{int(last.get('files_changed', 0)):>6} "
            f"{int(last.get('chunks_added', 0)):>8} "
            f"{int(last.get('chunks_deleted', 0)):>8} "
            f"{float(last.get('duration_s', 0)):>7.1f}"
        )
    return "\n".join(lines)
