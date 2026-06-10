from __future__ import annotations

import chromadb
import pytest

from grepsense import incremental, semantic
from grepsense.config import Config
from grepsense.state import get_state_collection, get_state_record
from tests.helpers import git_commit_all, init_git_repo


@pytest.fixture
def ephemeral_chroma(monkeypatch):
    client = chromadb.EphemeralClient()
    monkeypatch.setattr(incremental, "_chroma_client", lambda _cfg: client)
    monkeypatch.setattr(semantic, "load_model", lambda _name: object())
    return client


def test_baseline_then_incremental_no_encode(
    tmp_path, ephemeral_chroma, encode_call_counter, monkeypatch
) -> None:
    encode_fn, call_count = encode_call_counter
    monkeypatch.setattr(incremental, "model_encode_fn", lambda _m: encode_fn)

    root = tmp_path / "workspace"
    root.mkdir()
    init_git_repo(root / "myrepo", {"main.py": "code\n" * 80})
    cfg = Config(root=root, chroma_host="local", chroma_port=8000, collection="embedtest")

    result = incremental.run_once(cfg)
    assert result["chunks"] > 0
    state = get_state_record(get_state_collection(ephemeral_chroma), "myrepo")
    assert state["last_run"]["scope"] == "baseline"
    baseline_calls = call_count()

    result2 = incremental.run_once(cfg)
    assert result2["chunks"] == 0
    assert call_count() == baseline_calls
    state2 = get_state_record(get_state_collection(ephemeral_chroma), "myrepo")
    assert state2["last_run"]["scope"] == "incremental"


def test_reset_rebaselines(
    tmp_path, ephemeral_chroma, encode_call_counter, monkeypatch
) -> None:
    encode_fn, call_count = encode_call_counter
    monkeypatch.setattr(incremental, "model_encode_fn", lambda _m: encode_fn)

    root = tmp_path / "workspace"
    root.mkdir()
    init_git_repo(root / "myrepo", {"x.py": "x\n" * 80})
    cfg = Config(root=root, collection="embedtest")

    incremental.run_once(cfg)
    before = call_count()
    incremental.run_once(cfg, reset=True)
    state = get_state_record(get_state_collection(ephemeral_chroma), "myrepo")
    assert state is not None
    assert state["last_run"]["scope"] == "baseline"
    assert call_count() > before


def test_incremental_picks_up_committed_change(
    tmp_path, ephemeral_chroma, encode_call_counter, monkeypatch
) -> None:
    encode_fn, call_count = encode_call_counter
    monkeypatch.setattr(incremental, "model_encode_fn", lambda _m: encode_fn)

    root = tmp_path / "workspace"
    root.mkdir()
    repo = init_git_repo(root / "myrepo", {"main.py": "v1\n" * 80})
    cfg = Config(root=root, collection="embedtest")

    incremental.run_once(cfg)
    before = call_count()

    (repo / "main.py").write_text("v2\n" * 80)
    git_commit_all(repo, "update")
    result = incremental.run_once(cfg)
    assert result["chunks"] > 0
    assert call_count() > before
