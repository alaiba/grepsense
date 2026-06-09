# Install & Operations

## Prerequisites

- **Docker + Docker Compose** (the recommended path), or
- **Python 3.10+** for the native path.

## Docker Compose (recommended)

```bash
git clone https://github.com/alaiba/grepsense && cd grepsense
cp .env.example .env
$EDITOR .env            # set GREPSENSE_TARGET to your project's absolute path
docker compose up -d
```

What comes up:

| Service | Role | Port |
|---------|------|------|
| `zoekt-indexer` | builds the trigram index from `/code`, re-indexes on a loop | — |
| `zoekt-web` | serves lexical search | 6070 (internal) |
| `chromadb` | vector store for embeddings | 8000 (internal) |
| `embedder` | chunks + embeds `/code`, re-embeds on a loop | — |
| `mcp` | the MCP server agents connect to | **`GREPSENSE_HTTP_PORT`** (8765) |

First run downloads/builds the index and embeddings (minutes for a large repo).
Check progress with `docker compose logs -f embedder zoekt-indexer`.

### Verify

```bash
curl -s localhost:6070/search?q=TODO\&num=1\&format=json | head -c 200   # Zoekt
curl -s localhost:8000/api/v2/heartbeat                                  # ChromaDB
```

### Register with your agent (HTTP — works for all agents)

```bash
# Claude Code
claude mcp add --transport http grepsense http://localhost:8765/mcp
```

```toml
# Codex — ~/.codex/config.toml
[mcp_servers.grepsense]
url = "http://localhost:8765/mcp"
```

For **Cursor** and other MCP clients, add an HTTP MCP server in settings with URL
`http://localhost:8765/mcp`.

> Hosting for a team: run the stack on a shared host, expose the port (behind your
> own auth/proxy — grepsense does not add auth), and have everyone register the URL.

## Native (no Docker)

```bash
pipx install grepsense
grepsense version
```

You still need a Zoekt webserver and a ChromaDB reachable via `ZOEKT_URL` /
`CHROMADB_HOST`/`CHROMADB_PORT`. Then:

```bash
cd /path/to/project
grepsense embed                                   # build embeddings
grepsense serve --transport http --port 8765      # or: grepsense serve   (stdio)
claude mcp add grepsense -- grepsense serve        # stdio registration
```

## Operations

- **Re-index cadence:** `REINDEX_INTERVAL` (Zoekt, default 300s) and
  `EMBED_INTERVAL` (embeddings, default 3600s) in `.env`.
- **Force a rebuild:** `docker compose restart zoekt-indexer embedder`, or
  `grepsense embed --reset` to wipe + rebuild the collection.
- **Multiple projects:** run separate stacks with distinct `GREPSENSE_HTTP_PORT`,
  `GREPSENSE_COLLECTION`, `ZOEKT_VOLUME`, `CHROMADB_VOLUME` (e.g. via multiple
  `.env` files / `-p` project names).
- **Tear down (keep data):** `docker compose down`. **Wipe data too:**
  `docker compose down -v`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tool errors "Zoekt not reachable" | `docker compose ps`; check `zoekt-web` is up |
| Tool errors "ChromaDB not reachable" | check `chromadb` is up and `embedder` finished a pass |
| No semantic results | embeddings still building — watch `docker compose logs -f embedder` |
| Agent doesn't see tools | re-register the URL; restart/reload the agent session |
| Nothing indexed | confirm `GREPSENSE_TARGET` is a git repo (or a dir of git repos) |
