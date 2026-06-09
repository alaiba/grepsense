from __future__ import annotations

from dataclasses import replace

import httpx
from starlette.testclient import TestClient

from grepsense import server


class MockResponse:
    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def mcp_app():
    server.mcp._session_manager = None
    return server.mcp.streamable_http_app()


def test_healthz_returns_ok() -> None:
    with TestClient(mcp_app()) as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_returns_ok_when_dependencies_are_reachable(monkeypatch) -> None:
    seen_urls: list[str] = []

    def mock_get(url: str, timeout: float) -> MockResponse:
        seen_urls.append(url)
        return MockResponse()

    monkeypatch.setattr(server.httpx, "get", mock_get)
    monkeypatch.setattr(
        server,
        "CFG",
        replace(server.CFG, zoekt_url="http://zoekt-web:6070", chroma_host="chromadb"),
    )

    with TestClient(mcp_app()) as client:
        resp = client.get("/readyz")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    assert seen_urls == [
        "http://zoekt-web:6070/",
        "http://chromadb:8000/api/v2/heartbeat",
    ]


def test_readyz_fails_clearly_when_zoekt_is_unavailable(monkeypatch) -> None:
    def mock_get(url: str, timeout: float) -> MockResponse:
        if "zoekt-web" in url:
            raise httpx.ConnectError("connection refused")
        return MockResponse()

    monkeypatch.setattr(server.httpx, "get", mock_get)
    monkeypatch.setattr(
        server,
        "CFG",
        replace(server.CFG, zoekt_url="http://zoekt-web:6070", chroma_host="chromadb"),
    )

    with TestClient(mcp_app()) as client:
        resp = client.get("/readyz")

    body = resp.json()
    assert resp.status_code == 503
    assert body["status"] == "not_ready"
    assert body["dependencies"][0]["name"] == "zoekt"
    assert body["dependencies"][0]["ok"] is False
    assert "connection refused" in body["dependencies"][0]["error"]
    assert body["dependencies"][1]["ok"] is True


def test_readyz_fails_clearly_when_chromadb_is_unavailable(monkeypatch) -> None:
    def mock_get(url: str, timeout: float) -> MockResponse:
        if "api/v2/heartbeat" in url:
            return MockResponse(status_code=503, text="not available")
        return MockResponse()

    monkeypatch.setattr(server.httpx, "get", mock_get)
    monkeypatch.setattr(
        server,
        "CFG",
        replace(server.CFG, zoekt_url="http://zoekt-web:6070", chroma_host="chromadb"),
    )

    with TestClient(mcp_app()) as client:
        resp = client.get("/readyz")

    body = resp.json()
    assert resp.status_code == 503
    assert body["status"] == "not_ready"
    assert body["dependencies"][0]["ok"] is True
    assert body["dependencies"][1]["name"] == "chromadb"
    assert body["dependencies"][1]["ok"] is False
    assert body["dependencies"][1]["status_code"] == 503
    assert body["dependencies"][1]["error"] == "not available"
