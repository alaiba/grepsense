from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

from grepsense.gitchanges import changed_paths, current_head
from tests.helpers import git_commit_all, init_git_repo


def test_changed_paths_empty_when_clean(tmp_path) -> None:
    repo = init_git_repo(tmp_path / "repo")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    state = {
        "watermark": future,
        "head": current_head(repo),
    }
    assert changed_paths(repo, state) == set()


def test_changed_paths_detects_modified_file(tmp_path) -> None:
    repo = init_git_repo(tmp_path / "repo", {"a.py": "v1\n" * 30})
    state = {
        "watermark": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "head": current_head(repo),
    }
    (repo / "a.py").write_text("v2\n" * 30)
    paths = changed_paths(repo, state)
    assert "a.py" in paths


def test_changed_paths_detects_committed_change(tmp_path) -> None:
    repo = init_git_repo(tmp_path / "repo")
    state = {
        "watermark": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "head": current_head(repo),
    }
    (repo / "b.py").write_text("new file\n" * 30)
    git_commit_all(repo, "add b")
    paths = changed_paths(repo, state)
    assert "b.py" in paths


def test_changed_paths_detects_deleted_file(tmp_path) -> None:
    repo = init_git_repo(tmp_path / "repo", {"a.py": "x\n" * 30, "b.py": "y\n" * 30})
    state = {
        "watermark": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "head": current_head(repo),
    }
    (repo / "b.py").unlink()
    git_commit_all(repo, "delete b")
    paths = changed_paths(repo, state)
    assert "b.py" in paths


def test_changed_paths_detects_rename(tmp_path) -> None:
    repo = init_git_repo(tmp_path / "repo", {"old.py": "z\n" * 30})
    state = {
        "watermark": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "head": current_head(repo),
    }
    (repo / "old.py").rename(repo / "new.py")
    git_commit_all(repo, "rename")
    paths = changed_paths(repo, state)
    assert "new.py" in paths or "old.py" in paths


def test_changed_paths_head_jump_catches_rebase(tmp_path) -> None:
    repo = init_git_repo(tmp_path / "repo", {"a.py": "base\n" * 30})
    old_head = current_head(repo)
    (repo / "feature.py").write_text("feature\n" * 30)
    git_commit_all(repo, "feature")
    new_head = current_head(repo)
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    state = {"watermark": old_date, "head": old_head}
    paths = changed_paths(repo, state)
    assert "feature.py" in paths
    assert new_head != old_head


def test_changed_paths_respects_watermark_for_old_commits(tmp_path) -> None:
    repo = init_git_repo(tmp_path / "repo")
    head = current_head(repo)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    state = {"watermark": future, "head": head}
    assert changed_paths(repo, state) == set()
