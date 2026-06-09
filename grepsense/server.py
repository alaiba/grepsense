"""grepsense MCP server.

A single long-lived Python process exposing two tools — ``code_search`` (Zoekt
trigram/regex) and ``semantic_code_search`` (ChromaDB embeddings) — over MCP. The
embedding model loads once and stays warm. Speaks both ``stdio`` (local agents)
and ``streamable-http`` (a shared/hosted server).

It connects to already-running Zoekt and ChromaDB (the grepsense stack) and
returns a clear error if a service is unreachable.
"""

from __future__ import annotations

from typing import Annotated, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import semantic
from .config import Config

CFG = Config.load()
mcp = FastMCP("grepsense")


def _reachable(
    url: str,
    timeout: float = 3.0,
    params: dict[str, str] | None = None,
) -> bool:
    try:
        return httpx.get(url, params=params, timeout=timeout).is_success
    except Exception:
        return False


def _dependency_status(
    name: str,
    url: str,
    params: dict[str, str] | None = None,
) -> dict:
    try:
        resp = httpx.get(url, params=params, timeout=3.0)
    except Exception as err:
        return {"name": name, "ok": False, "url": url, "error": str(err)}

    status: dict = {
        "name": name,
        "ok": resp.is_success,
        "url": url,
        "status_code": resp.status_code,
    }
    if not resp.is_success:
        status["error"] = resp.text[:500]
    return status


@mcp.custom_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET", "HEAD"], include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    dependencies = [
        _dependency_status(
            "zoekt",
            CFG.zoekt_search_url,
            params={"q": "TODO", "num": "1", "format": "json"},
        ),
        _dependency_status("chromadb", CFG.chroma_heartbeat_url),
    ]
    ready = all(dep["ok"] for dep in dependencies)
    status_code = 200 if ready else 503
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "dependencies": dependencies},
        status_code=status_code,
    )


@mcp.tool()
def code_search(
    query: Annotated[
        str,
        Field(description=(
            "Search query. Supports Zoekt syntax: literal text, regex, "
            "repo:<name>, file:<pattern>, lang:<language>, case:yes"
        )),
    ],
    max_results: Annotated[
        int, Field(description="Maximum number of file matches to return (default 20)")
    ] = 20,
) -> str:
    """Search source code using a trigram index (Zoekt). Supports literal strings, regex, and filters like repo:, file:, lang:, case:yes. Fast sub-10ms across all indexed repos."""
    if not _reachable(
        CFG.zoekt_search_url,
        params={"q": "TODO", "num": "1", "format": "json"},
    ):
        raise RuntimeError(
            f"Zoekt is not reachable at {CFG.zoekt_url}. Is the grepsense stack up? "
            "(docker compose up -d)"
        )

    try:
        resp = httpx.get(
            CFG.zoekt_search_url,
            params={"q": query, "num": max_results, "format": "json"},
            timeout=30.0,
        )
    except Exception as err:
        raise RuntimeError(f"Error querying Zoekt: {err}")

    if not resp.is_success:
        raise RuntimeError(f"Zoekt returned HTTP {resp.status_code}: {resp.text}")

    result = (resp.json() or {}).get("result") or {}
    stats = result.get("Stats") or {}
    matches = result.get("FileMatches") or []

    duration_ms = (stats.get("Duration", 0) or 0) / 1e6
    lines = [
        f"Found {stats.get('MatchCount', 0)} matches across "
        f"{stats.get('FileCount', 0)} files ({duration_ms:.1f}ms)",
        "",
    ]
    for file in matches:
        repo = file["Repo"].split("/")[-1]
        lines.append(f"## {repo}/{file['FileName']}")
        for match in file.get("Matches") or []:
            frags = "".join(
                f"{f.get('Pre', '')}«{f.get('Match', '')}»{f.get('Post', '')}"
                for f in (match.get("Fragments") or [])
            )
            lines.append(f"  L{match['LineNum']}: {frags.strip()}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def semantic_code_search(
    query: Annotated[
        str,
        Field(description=(
            "Natural language description of what the code does, e.g. "
            "'middleware that validates user session cookies'"
        )),
    ],
    max_results: Annotated[
        int, Field(description="Maximum number of code chunks to return (default 10)")
    ] = 10,
    repo_filter: Annotated[
        Optional[str], Field(description="Filter results to a specific repo name")
    ] = None,
) -> str:
    """Search code by meaning using embeddings (ChromaDB). Use when you need code that does something but don't know the exact class/function name."""
    if not _reachable(CFG.chroma_heartbeat_url):
        raise RuntimeError(
            f"ChromaDB is not reachable at {CFG.chroma_host}:{CFG.chroma_port}. "
            "Is the grepsense stack up? (docker compose up -d)"
        )

    try:
        results = semantic.search(
            query,
            host=CFG.chroma_host,
            port=CFG.chroma_port,
            collection=CFG.collection,
            model_name=CFG.embedding_model,
            max_results=max_results,
            repo_filter=repo_filter,
        )
    except Exception as err:
        raise RuntimeError(
            f"Semantic search unavailable: {err}. Build embeddings with "
            "`grepsense embed` (or wait for the embedder service)."
        )

    if not results:
        return "No semantic matches found for the query."

    lines = [f"Found {len(results)} semantic matches:\n"]
    for r in results:
        meta = r.get("metadata") or {}
        lines.append(
            f"## {meta.get('repo', 'unknown')}/{meta.get('file_path', r.get('id'))} "
            f"(score: {r.get('score')})"
        )
        if meta.get("start_line"):
            lines.append(f"Lines {meta.get('start_line')}-{meta.get('end_line', '?')}")
        if meta.get("language"):
            lines.append(f"Language: {meta.get('language')}")
        doc = r.get("document") or "(content not stored)"
        lines.append("```")
        lines.append(doc[:500] + "..." if len(doc) > 500 else doc)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def run(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    if transport == "http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
