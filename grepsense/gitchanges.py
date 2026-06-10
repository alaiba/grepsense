"""Git-based changed-path detection for incremental embedding."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def current_head(repo_path: Path) -> str:
    return _run_git(repo_path, "rev-parse", "HEAD").strip()


def committed_changes_since(repo_path: Path, watermark: str) -> set[str]:
    if not watermark:
        return set()
    try:
        out = _run_git(
            repo_path,
            "log",
            f"--since={watermark}",
            "--name-only",
            "--diff-filter=ACMRTD",
            "--pretty=format:",
        )
    except subprocess.CalledProcessError:
        return set()
    paths: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if line:
            paths.add(line)
    return paths


def diff_paths_between_heads(repo_path: Path, prev_head: str, head: str) -> set[str]:
    if not prev_head or prev_head == head:
        return set()
    try:
        out = _run_git(repo_path, "diff", "--name-only", f"{prev_head}..{head}")
    except subprocess.CalledProcessError:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def _parse_watermark(watermark: str) -> datetime | None:
    if not watermark:
        return None
    try:
        dt = datetime.fromisoformat(watermark.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _path_touched_since(repo_path: Path, rel_path: str, since: datetime) -> bool:
    path = repo_path / rel_path
    if not path.exists():
        return True
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return mtime >= since


def uncommitted_changes(repo_path: Path, watermark: str) -> set[str]:
    since = _parse_watermark(watermark)
    try:
        out = _run_git(repo_path, "status", "--porcelain", "-z")
    except subprocess.CalledProcessError:
        return set()
    if not out:
        return set()

    paths: set[str] = set()
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry or len(entry) < 3:
            continue
        status = entry[:2]
        path = entry[3:]
        if status.startswith("R") and i < len(entries):
            old_path = path
            new_path = entries[i]
            i += 1
            candidates = {old_path, new_path}
        else:
            candidates = {path}
        for candidate in candidates:
            if since is None or _path_touched_since(repo_path, candidate, since):
                paths.add(candidate)
    return paths


def changed_paths(repo_path: Path, state: dict | None) -> set[str]:
    """Return repo-relative paths that changed since the last successful embed."""
    if state is None:
        return set()

    watermark = state.get("watermark") or ""
    prev_head = state.get("head") or ""
    head = current_head(repo_path)

    paths: set[str] = set()
    if prev_head and head != prev_head:
        paths.update(diff_paths_between_heads(repo_path, prev_head, head))
    elif not prev_head:
        paths.update(committed_changes_since(repo_path, watermark))
    paths.update(uncommitted_changes(repo_path, watermark))
    return paths
