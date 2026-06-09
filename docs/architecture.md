# Architecture

grepsense has two search layers behind one MCP server.

```
┌─────────────────────────────────────────────────┐
│  zoekt-indexer → zoekt-web (:6070)              │
│  trigram index over your git repos; regex/      │
│  literal search, sub-10ms, repo/file/lang filters│
└─────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────┐
│  embedder → chromadb (:8000)                    │
│  line-based chunks, all-MiniLM-L6-v2 embeddings,│
│  cosine similarity                              │
└─────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────┐
│  grepsense MCP server (:8765)                   │
│  code_search · semantic_code_search             │
│  Python · warm model · stdio / streamable-HTTP  │
└─────────────────────────────────────────────────┘
```

## Layer 1 — Zoekt (lexical)

Google's open-source trigram engine (the core of Sourcegraph). A small Go binary
builds a compact index (~⅓ of source) and serves regex/literal queries with a
JSON API. We build it from a pinned upstream commit (no patches) — see `zoekt/`.
Indexing honors each repo's `.gitignore`.

## Layer 2 — ChromaDB + embeddings (semantic)

Source files are chunked by line boundaries (1500 chars, 200 overlap), embedded
with a local model (`all-MiniLM-L6-v2`, no API key), and stored in ChromaDB with
cosine similarity. The embedder (`grepsense embed`) discovers repos, walks them
(honoring include/exclude globs), and upserts chunks keyed by a content hash.

## Layer 3 — MCP server

`grepsense.server` uses the MCP Python SDK (`FastMCP`). One long-lived process, so
the embedding model loads **once and stays warm** — semantic queries don't pay a
cold load per call. It exposes `stdio` (local agents) and `streamable-http`
(shared/hosted) transports, and connects to already-running Zoekt + ChromaDB,
returning a clear error if a backend is down. Configuration is env-driven
(`grepsense.config.Config`), optionally overridden by `grepsense.yaml`.

For HTTP deployments, MCP also exposes `GET /healthz` and `GET /readyz`.
`/healthz` is a process liveness check only. `/readyz` checks dependency
reachability against `ZOEKT_URL` and ChromaDB's `/api/v2/heartbeat`, returning
JSON details and a non-200 status when either dependency is unavailable.

## Repository discovery

`grepsense.discovery` derives targets from the git layout of `GREPSENSE_ROOT`:
if the root is itself a git repo it is indexed as one (the effective root shifts
to its parent so the universal `root/<name>` path model holds); otherwise each
child git repo is indexed. No editor/workspace files required.

## Deployment

- **Compose (primary):** every component runs in a container; `docker compose up`
  builds the index + embeddings and serves MCP over HTTP. Indexes persist in the
  `grepsense-zoekt-index` and `grepsense-chroma-data` volumes.
- **Native:** `pipx install grepsense` + a reachable Zoekt and ChromaDB; the
  server runs over stdio or HTTP.
- **Kubernetes / Helm:** the chart deploys the five runtime components with
  HTTP/TCP probes for MCP, Zoekt web, and ChromaDB. The `zoekt-indexer` and
  `embedder` workers only have liveness probes initially because they are
  background loops without a reliable readiness signal.

Both the Zoekt index and the embeddings are fully regenerable from source, so
losing them only costs re-index time.
