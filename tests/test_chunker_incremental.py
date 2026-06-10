from __future__ import annotations

import chromadb

from grepsense.chunker import (
    chunk_file,
    delete_chunks_for_paths,
    embed_paths,
    embed_repo,
    make_chunk_id,
)
from grepsense.config import Config
from tests.helpers import init_git_repo


def test_modify_one_file_encodes_only_its_chunks(
    tmp_path, chroma_client: chromadb.EphemeralClient, encode_call_counter
) -> None:
    encode_fn, call_count = encode_call_counter
    root = tmp_path / "workspace"
    root.mkdir()
    repo = init_git_repo(
        root / "myrepo",
        {
            "a.py": "alpha\n" * 80,
            "b.py": "beta\n" * 80,
        },
    )
    collection = chroma_client.get_or_create_collection(
        name="data", metadata={"hnsw:space": "cosine"}
    )
    cfg = Config(root=root)

    embed_repo(collection, encode_fn, cfg, "myrepo", repo)
    baseline_calls = call_count()

    (repo / "a.py").write_text("changed alpha\n" * 80)
    delete_chunks_for_paths(collection, "myrepo", {"a.py"})
    embed_paths(collection, encode_fn, cfg, "myrepo", repo, {"a.py"})

    incremental_calls = call_count() - baseline_calls
    a_chunks = len(chunk_file(repo / "a.py", cfg.max_chunk_size, cfg.overlap, cfg.min_chunk_size))
    assert incremental_calls == a_chunks


def test_delete_chunks_removes_file_vectors(
    tmp_path, chroma_client: chromadb.EphemeralClient, encode_call_counter
) -> None:
    encode_fn, _ = encode_call_counter
    root = tmp_path / "workspace"
    root.mkdir()
    repo = init_git_repo(root / "myrepo", {"gone.py": "bye\n" * 80})
    collection = chroma_client.get_or_create_collection(
        name="data", metadata={"hnsw:space": "cosine"}
    )
    cfg = Config(root=root)
    embed_repo(collection, encode_fn, cfg, "myrepo", repo)
    assert collection.count() > 0

    delete_chunks_for_paths(collection, "myrepo", {"gone.py"})
    result = collection.get(where={"file_path": "gone.py"}, include=[])
    assert result.get("ids") == []
