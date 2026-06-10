from __future__ import annotations

from grepsense.chunker import flush_batch


def test_flush_batch_uses_injected_encode_fn(chroma_client, encode_call_counter) -> None:
    encode_fn, call_count = encode_call_counter
    collection = chroma_client.get_or_create_collection(
        name="test", metadata={"hnsw:space": "cosine"}
    )

    count = flush_batch(
        collection,
        encode_fn,
        ids=["a"],
        docs=["hello"],
        metas=[{"repo": "r", "file_path": "f.py", "start_line": 1, "end_line": 1, "language": "python"}],
    )

    assert count == 1
    assert call_count() == 1
    assert collection.count() == 1
