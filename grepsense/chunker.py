"""Chunk source files and embed them into ChromaDB for semantic search."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from . import semantic
from .config import Config
from .discovery import resolve_targets

EXT_LANG = {
    ".java": "java", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".py": "python",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".kt": "kotlin", ".php": "php", ".sh": "bash", ".sql": "sql",
    ".gradle": "groovy", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".md": "markdown", ".toml": "toml",
}

EncodeFn = Callable[[list[str]], list[list[float]]]


def detect_language(path: Path) -> str:
    return EXT_LANG.get(path.suffix.lower(), "unknown")


def should_exclude(rel_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if f"/{pattern}" in f"/{rel_path}/" or rel_path.startswith(pattern):
                return True
        elif fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
            os.path.basename(rel_path), pattern
        ):
            return True
    return False


def collect_files(
    repo_path: Path,
    include_extensions: set[str],
    exclude_patterns: list[str],
    *,
    only_paths: set[str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    for root, dirs, filenames in os.walk(repo_path):
        rel_root = os.path.relpath(root, repo_path)
        if should_exclude(rel_root + "/", exclude_patterns):
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            if Path(fname).suffix.lower() not in include_extensions:
                continue
            rel_file = os.path.join(rel_root, fname) if rel_root != "." else fname
            if should_exclude(rel_file, exclude_patterns):
                continue
            if only_paths is not None and rel_file not in only_paths:
                continue
            files.append(Path(root) / fname)
    return files


def chunk_file(
    file_path: Path, max_chunk_size: int, overlap: int, min_chunk_size: int
) -> list[dict]:
    """Split a file into overlapping line-based chunks."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []
    if len(content.strip()) < min_chunk_size:
        return []

    lines = content.split("\n")
    chunks: list[dict] = []
    current: list[str] = []
    current_size = 0
    start_line = 1

    for i, line in enumerate(lines, 1):
        current.append(line)
        current_size += len(line) + 1
        if current_size >= max_chunk_size:
            text = "\n".join(current)
            if len(text.strip()) >= min_chunk_size:
                chunks.append({"text": text, "start_line": start_line, "end_line": i})
            tail: list[str] = []
            tail_size = 0
            for ln in reversed(current):
                tail_size += len(ln) + 1
                tail.insert(0, ln)
                if tail_size >= overlap:
                    break
            current = tail
            current_size = sum(len(ln) + 1 for ln in current)
            start_line = i - len(current) + 1

    if current:
        text = "\n".join(current)
        if len(text.strip()) >= min_chunk_size:
            chunks.append({"text": text, "start_line": start_line, "end_line": len(lines)})
    return chunks


def make_chunk_id(repo: str, rel: str, start_line: int, end_line: int, idx: int) -> str:
    return hashlib.sha256(
        f"{repo}:{rel}:{start_line}:{end_line}:{idx}".encode()
    ).hexdigest()[:32]


def chunk_metadata(repo: str, rel: str, lang: str, ch: dict) -> dict[str, Any]:
    return {
        "repo": repo,
        "file_path": rel,
        "start_line": ch["start_line"],
        "end_line": ch["end_line"],
        "language": lang,
    }


def _dedupe_batch(ids: list[str], docs: list[str], metas: list[dict]) -> tuple[list[str], list[str], list[dict]]:
    seen: set[str] = set()
    d_ids, d_docs, d_metas = [], [], []
    for i, cid in enumerate(ids):
        if cid not in seen:
            seen.add(cid)
            d_ids.append(cid)
            d_docs.append(docs[i])
            d_metas.append(metas[i])
    return d_ids, d_docs, d_metas


def flush_batch(
    collection: Any,
    encode_fn: EncodeFn,
    ids: list[str],
    docs: list[str],
    metas: list[dict],
) -> int:
    if not ids:
        return 0
    d_ids, d_docs, d_metas = _dedupe_batch(ids, docs, metas)
    embeddings = encode_fn(d_docs)
    collection.upsert(ids=d_ids, documents=d_docs, metadatas=d_metas, embeddings=embeddings)
    return len(d_ids)


def model_encode_fn(model: Any) -> EncodeFn:
    def encode(docs: list[str]) -> list[list[float]]:
        return model.encode(docs).tolist()

    return encode


def _batched(items: Iterable[str], size: int) -> list[list[str]]:
    batch: list[str] = []
    batches: list[list[str]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            batches.append(batch)
            batch = []
    if batch:
        batches.append(batch)
    return batches


def embed_paths(
    collection: Any,
    encode_fn: EncodeFn,
    config: Config,
    repo: str,
    repo_path: Path,
    paths: set[str],
    *,
    batch_size: int = 100,
) -> int:
    """Chunk and embed only the given paths (relative to repo root)."""
    include = set(config.include_extensions)
    existing = {p for p in paths if (repo_path / p).is_file()}
    files = collect_files(
        repo_path, include, config.exclude_patterns, only_paths=existing
    )
    ids, docs, metas = [], [], []
    total = 0
    for fp in files:
        rel = str(fp.relative_to(repo_path))
        lang = detect_language(fp)
        for idx, ch in enumerate(
            chunk_file(fp, config.max_chunk_size, config.overlap, config.min_chunk_size)
        ):
            ids.append(make_chunk_id(repo, rel, ch["start_line"], ch["end_line"], idx))
            docs.append(ch["text"])
            metas.append(chunk_metadata(repo, rel, lang, ch))
            if len(ids) >= batch_size:
                total += flush_batch(collection, encode_fn, ids, docs, metas)
                ids, docs, metas = [], [], []
    if ids:
        total += flush_batch(collection, encode_fn, ids, docs, metas)
    return total


def embed_repo(
    collection: Any,
    encode_fn: EncodeFn,
    config: Config,
    repo: str,
    repo_path: Path,
    *,
    batch_size: int = 100,
) -> tuple[int, int]:
    """Full-repo embed. Returns (chunks_embedded, file_count)."""
    include = set(config.include_extensions)
    files = collect_files(repo_path, include, config.exclude_patterns)
    ids, docs, metas = [], [], []
    total = 0
    for fp in files:
        rel = str(fp.relative_to(repo_path))
        lang = detect_language(fp)
        for idx, ch in enumerate(
            chunk_file(fp, config.max_chunk_size, config.overlap, config.min_chunk_size)
        ):
            ids.append(make_chunk_id(repo, rel, ch["start_line"], ch["end_line"], idx))
            docs.append(ch["text"])
            metas.append(chunk_metadata(repo, rel, lang, ch))
            if len(ids) >= batch_size:
                total += flush_batch(collection, encode_fn, ids, docs, metas)
                ids, docs, metas = [], [], []
    if ids:
        total += flush_batch(collection, encode_fn, ids, docs, metas)
    return total, len(files)


def delete_chunks_for_paths(
    collection: Any,
    repo: str,
    paths: set[str],
    *,
    batch_size: int = 1000,
) -> int:
    """Delete all chunks for the given repo paths. Returns chunks removed."""
    if not paths:
        return 0
    deleted = 0
    for batch in _batched(sorted(paths), batch_size):
        before = collection.count()
        collection.delete(
            where={"$and": [{"repo": repo}, {"file_path": {"$in": batch}}]}
        )
        after = collection.count()
        deleted += max(0, before - after)
    return deleted


def embed_repo_fallback_skip(
    collection: Any,
    encode_fn: EncodeFn,
    config: Config,
    repo: str,
    repo_path: Path,
    *,
    batch_size: int = 100,
    id_lookup_batch: int = 500,
) -> tuple[int, int]:
    """Walk a non-git tree; encode only chunks whose IDs are not yet in Chroma."""
    include = set(config.include_extensions)
    files = collect_files(repo_path, include, config.exclude_patterns)
    ids, docs, metas = [], [], []
    total = 0
    pending_ids: list[str] = []

    def flush_pending() -> None:
        nonlocal total, ids, docs, metas, pending_ids
        if not ids:
            return
        existing: set[str] = set()
        for i in range(0, len(pending_ids), id_lookup_batch):
            batch = pending_ids[i : i + id_lookup_batch]
            result = collection.get(ids=batch, include=[])
            existing.update(result.get("ids") or [])
        new_ids, new_docs, new_metas = [], [], []
        for j, cid in enumerate(pending_ids):
            if cid not in existing:
                new_ids.append(cid)
                new_docs.append(docs[j])
                new_metas.append(metas[j])
        if new_ids:
            total += flush_batch(collection, encode_fn, new_ids, new_docs, new_metas)
        ids, docs, metas, pending_ids = [], [], [], []

    for fp in files:
        rel = str(fp.relative_to(repo_path))
        lang = detect_language(fp)
        for idx, ch in enumerate(
            chunk_file(fp, config.max_chunk_size, config.overlap, config.min_chunk_size)
        ):
            cid = make_chunk_id(repo, rel, ch["start_line"], ch["end_line"], idx)
            ids.append(cid)
            docs.append(ch["text"])
            metas.append(chunk_metadata(repo, rel, lang, ch))
            pending_ids.append(cid)
            if len(ids) >= batch_size:
                flush_pending()
    flush_pending()
    return total, len(files)


def embed(
    config: Config,
    *,
    repo_filter: str | None = None,
    reset: bool = False,
    batch_size: int = 100,
) -> dict:
    """Walk the configured repos, chunk + embed their files into ChromaDB."""
    import chromadb

    effective_root, repos = resolve_targets(config.root)
    if not repos:
        raise RuntimeError(f"grepsense: no git repos found under {config.root}")
    if repo_filter:
        repos = [r for r in repos if r == repo_filter]
        if not repos:
            raise RuntimeError(f"grepsense: repo '{repo_filter}' not found under {config.root}")

    client = chromadb.HttpClient(host=config.chroma_host, port=config.chroma_port)
    if reset:
        try:
            client.delete_collection(config.collection)
        except Exception:
            pass
    collection = client.get_or_create_collection(
        name=config.collection, metadata={"hnsw:space": "cosine"}
    )
    model = semantic.load_model(config.embedding_model)
    encode_fn = model_encode_fn(model)

    total_chunks = 0
    total_files = 0

    for name in repos:
        repo_path = effective_root / name
        if not repo_path.is_dir():
            continue
        chunks, file_count = embed_repo(
            collection, encode_fn, config, name, repo_path, batch_size=batch_size
        )
        total_chunks += chunks
        total_files += file_count
        print(f"  {name}: {file_count} files")

    return {
        "repos": repos,
        "files": total_files,
        "chunks": total_chunks,
        "collection_count": collection.count(),
    }
