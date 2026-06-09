# grepsense

**Two-modal code search for AI coding agents.** grepsense gives your agent two
tools over [MCP](https://modelcontextprotocol.io):

- **`code_search`** — fast lexical search (regex/literal, `repo:`/`file:`/`lang:`
  filters) via [Zoekt](https://github.com/sourcegraph/zoekt), the trigram engine
  behind Sourcegraph. Sub-10ms.
- **`semantic_code_search`** — meaning-based search via local embeddings
  ([ChromaDB](https://www.trychroma.com/) + `all-MiniLM-L6-v2`). Find code by what
  it *does* when you don't know the exact name.

It's a single Python MCP server (the embedding model loads once and stays warm)
in front of a self-hosted engine. Drop it next to any repo, `docker compose up`,
and point your agent at the URL.

> Status: early. Works end-to-end; APIs and layout may still shift.

## Quick start (Docker Compose — recommended)

```bash
git clone https://github.com/alaiba/grepsense && cd grepsense
cp .env.example .env
#   edit .env → set GREPSENSE_TARGET=/absolute/path/to/your/project
docker compose up -d        # starts Zoekt + ChromaDB + embedder, builds the
                            # index, and serves the MCP server on :8765
```

Then register the server with your agent (one time):

```bash
# Claude Code
claude mcp add --transport http grepsense http://localhost:8765/mcp
```

| Agent | How to register |
|-------|-----------------|
| **Claude Code** | `claude mcp add --transport http grepsense http://localhost:8765/mcp` |
| **Codex** | add a `[mcp_servers.grepsense]` block in `~/.codex/config.toml` pointing at the URL |
| **Cursor / others** | add an MCP server in settings with URL `http://localhost:8765/mcp` |

The same running stack can serve many clients — run it on a shared host and point
everyone's agent at it. The MCP server only needs to reach Zoekt + ChromaDB; only
the indexer/embedder mount your source.
For Kubernetes/Helm deployments, the chart under `charts/grepsense` enables health probes by default via `probes.enabled`, and exposes component-level overrides for `mcp.probes`, `chromadb.probes`, `zoektWeb.probes`, `zoektIndexer.probes`, and `embedder.probes`.
MCP exposes lightweight HTTP health probes on the same port:

- `GET /healthz` returns 200 when the MCP process is alive.
- `GET /readyz` returns 200 only when MCP can reach Zoekt and ChromaDB.

## Alternative: native install (no Docker)

```bash
pipx install grepsense
cd /path/to/your/project
grepsense embed                      # build semantic embeddings (needs a running ChromaDB)
grepsense serve --transport http     # or stdio
claude mcp add grepsense -- grepsense serve   # stdio registration
```

(Native mode still needs a Zoekt webserver and a ChromaDB for the two backends;
Compose is the batteries-included path.)

## How it works

```
your code ──► zoekt-indexer ──► zoekt-web (:6070) ─┐
          └─► embedder ──────► chromadb (:8000) ───┤
                                                   ▼
                                   grepsense MCP server (:8765)
                                   code_search · semantic_code_search
                                   stdio · streamable-HTTP
```

See [docs/architecture.md](docs/architecture.md) for the design and
[docs/install.md](docs/install.md) for the full install/operations guide.

## Configuration

Everything is env-driven (see `.env.example`); a `grepsense.yaml` can override
defaults (include/exclude globs, chunking, embedding model). Key vars:

| Var | Purpose | Default |
|-----|---------|---------|
| `GREPSENSE_TARGET` | host path to index (Compose) | — (required) |
| `GREPSENSE_HTTP_PORT` | published MCP port | `8765` |
| `GREPSENSE_COLLECTION` | embeddings namespace | `grepsense` |
| `GREPSENSE_EMBEDDING_MODEL` | sentence-transformers model | `all-MiniLM-L6-v2` |
| `ZOEKT_URL` / `CHROMADB_HOST` / `CHROMADB_PORT` | backend locations | localhost defaults |

## License

MIT © Vasile Alaiba
