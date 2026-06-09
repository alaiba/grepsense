"""Shared semantic-search logic (embedding model + ChromaDB query).

Used by both the embedder and the MCP server. Heavy imports are deferred so that
importing this module stays cheap — the model is only loaded on first use and
then kept warm for the lifetime of the process.
"""

from __future__ import annotations

import threading

_model_cache: dict = {}
_model_lock = threading.Lock()


def load_model(model_name: str):
    """Load and cache a SentenceTransformer model (thread-safe, process-wide)."""
    model = _model_cache.get(model_name)
    if model is None:
        with _model_lock:
            model = _model_cache.get(model_name)
            if model is None:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(model_name)
                _model_cache[model_name] = model
    return model


def get_collection(host: str, port: int, collection: str):
    """Return the ChromaDB collection (raises if it does not exist yet)."""
    import chromadb

    client = chromadb.HttpClient(host=host, port=int(port))
    return client.get_collection(collection)


def search(
    query: str,
    *,
    host: str = "localhost",
    port: int = 8000,
    collection: str = "grepsense",
    model_name: str = "all-MiniLM-L6-v2",
    max_results: int = 10,
    repo_filter: str | None = None,
) -> list[dict]:
    """Embed ``query`` and return ranked semantic matches.

    Each result is ``{"id", "score", "metadata", "document"}`` where ``score`` is
    ``round(1 - cosine_distance, 4)``.
    """
    coll = get_collection(host, port, collection)
    model = load_model(model_name)
    query_embedding = model.encode([query]).tolist()

    kwargs: dict = {
        "query_embeddings": query_embedding,
        "n_results": max_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if repo_filter:
        kwargs["where"] = {"repo": repo_filter}

    results = coll.query(**kwargs)

    ids = results.get("ids", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    return [
        {
            "id": ids[i],
            "score": round(1 - distances[i], 4),
            "metadata": metadatas[i],
            "document": documents[i],
        }
        for i in range(len(ids))
    ]
