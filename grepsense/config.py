"""Configuration for grepsense.

Precedence (lowest to highest): built-in defaults < ``grepsense.yaml`` < env vars.
A single ``Config`` is shared by the embedder and the MCP server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - yaml is a declared dependency
    yaml = None

DEFAULT_INCLUDE_EXTENSIONS = [
    ".java", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".rb",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".kt", ".php", ".sh",
    ".sql", ".gradle", ".xml", ".yaml", ".yml", ".json", ".md", ".toml",
]

DEFAULT_EXCLUDE_PATTERNS = [
    "node_modules/", "build/", "out/", "dist/", ".gradle/", ".nx/",
    "vendor/", "__pycache__/", "coverage/", "test-results/",
    "*.min.js", "*.min.css", "*.map", "*.lock", "package-lock.json",
]


@dataclass
class Config:
    root: Path = field(default_factory=lambda: Path(".").resolve())
    zoekt_url: str = "http://localhost:6070"
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    collection: str = "grepsense"
    embedding_model: str = "all-MiniLM-L6-v2"
    include_extensions: list = field(default_factory=lambda: list(DEFAULT_INCLUDE_EXTENSIONS))
    exclude_patterns: list = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    max_chunk_size: int = 1500
    overlap: int = 200
    min_chunk_size: int = 50

    @classmethod
    def load(cls, config_path: str | os.PathLike | None = None) -> "Config":
        c = cls()

        # 1. grepsense.yaml (optional)
        path = Path(config_path) if config_path else Path(
            os.environ.get("GREPSENSE_CONFIG", "grepsense.yaml")
        )
        data: dict = {}
        if yaml is not None and path.exists():
            data = yaml.safe_load(path.read_text()) or {}

        if "root" in data:
            c.root = Path(data["root"])
        if "embedding_model" in data:
            c.embedding_model = data["embedding_model"]
        if "include_extensions" in data:
            c.include_extensions = data["include_extensions"]
        if "exclude_patterns" in data:
            c.exclude_patterns = data["exclude_patterns"]
        chroma = data.get("chromadb", {}) or {}
        c.chroma_host = chroma.get("host", c.chroma_host)
        c.chroma_port = int(chroma.get("port", c.chroma_port))
        c.collection = chroma.get("collection", c.collection)
        chunk = data.get("chunking", {}) or {}
        c.max_chunk_size = int(chunk.get("max_chunk_size", c.max_chunk_size))
        c.overlap = int(chunk.get("overlap", c.overlap))
        c.min_chunk_size = int(chunk.get("min_chunk_size", c.min_chunk_size))

        # 2. env vars (highest precedence)
        c.root = Path(os.environ.get("GREPSENSE_ROOT", str(c.root))).resolve()
        c.zoekt_url = os.environ.get("ZOEKT_URL", c.zoekt_url)
        c.chroma_host = os.environ.get("CHROMADB_HOST", c.chroma_host)
        c.chroma_port = int(os.environ.get("CHROMADB_PORT", c.chroma_port))
        c.collection = os.environ.get(
            "GREPSENSE_COLLECTION", os.environ.get("CHROMADB_COLLECTION", c.collection)
        )
        c.embedding_model = os.environ.get(
            "GREPSENSE_EMBEDDING_MODEL", os.environ.get("EMBEDDING_MODEL", c.embedding_model)
        )
        return c

    @property
    def zoekt_search_url(self) -> str:
        return f"{self.zoekt_url.rstrip('/')}/search"

    @property
    def chroma_heartbeat_url(self) -> str:
        return f"http://{self.chroma_host}:{self.chroma_port}/api/v2/heartbeat"
