"""Chunk source files and embed them into ChromaDB for semantic search."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path

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
    repo_path: Path, include_extensions: set[str], exclude_patterns: list[str]
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
            if not should_exclude(rel_file, exclude_patterns):
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
            # carry an overlap tail into the next chunk
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


def _flush(collection, model, ids, docs, metas) -> int:
    if not ids:
        return 0
    seen: set[str] = set()
    d_ids, d_docs, d_metas = [], [], []
    for i, cid in enumerate(ids):
        if cid not in seen:
            seen.add(cid)
            d_ids.append(cid)
            d_docs.append(docs[i])
            d_metas.append(metas[i])
    embeddings = model.encode(d_docs).tolist()
    collection.upsert(ids=d_ids, documents=d_docs, metadatas=d_metas, embeddings=embeddings)
    return len(d_ids)


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

    include = set(config.include_extensions)
    total_chunks = 0
    total_files = 0

    for name in repos:
        repo_path = effective_root / name
        if not repo_path.is_dir():
            continue
        files = collect_files(repo_path, include, config.exclude_patterns)
        ids, docs, metas = [], [], []
        for fp in files:
            rel = str(fp.relative_to(repo_path))
            lang = detect_language(fp)
            for idx, ch in enumerate(
                chunk_file(fp, config.max_chunk_size, config.overlap, config.min_chunk_size)
            ):
                cid = hashlib.sha256(
                    f"{name}:{rel}:{ch['start_line']}:{ch['end_line']}:{idx}".encode()
                ).hexdigest()[:32]
                ids.append(cid)
                docs.append(ch["text"])
                metas.append({
                    "repo": name,
                    "file_path": rel,
                    "start_line": ch["start_line"],
                    "end_line": ch["end_line"],
                    "language": lang,
                })
                if len(ids) >= batch_size:
                    total_chunks += _flush(collection, model, ids, docs, metas)
                    ids, docs, metas = [], [], []
        if ids:
            total_chunks += _flush(collection, model, ids, docs, metas)
        total_files += len(files)
        print(f"  {name}: {len(files)} files")

    return {
        "repos": repos,
        "files": total_files,
        "chunks": total_chunks,
        "collection_count": collection.count(),
    }
