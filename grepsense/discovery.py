"""Repository discovery from the on-disk git layout.

grepsense is pointed at a root (``GREPSENSE_ROOT``, default the current dir):

* If the root is itself a git repo, it is indexed as one repo and the *effective
  root* shifts to its parent so the universal ``root/<name>`` path model holds.
* Otherwise every immediate child directory that is a git repo is indexed.
"""

from __future__ import annotations

from pathlib import Path


def resolve_targets(root: str | Path) -> tuple[Path, list[str]]:
    """Return ``(effective_root, repo_names)`` for the given root."""
    root = Path(root).resolve()
    if (root / ".git").exists():
        return root.parent, [root.name]
    try:
        children = sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and (child / ".git").exists()
        )
    except OSError:
        children = []
    return root, children


def repo_names(root: str | Path) -> list[str]:
    return resolve_targets(root)[1]
