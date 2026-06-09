#!/usr/bin/env bash
set -euo pipefail

ROOT="${GREPSENSE_ROOT:-/code}"
INDEX_DIR="${ZOEKT_INDEX_DIR:-/data/index}"

mkdir -p "$INDEX_DIR"

if [ -d "$ROOT/.git" ]; then
  zoekt-git-index -index "$INDEX_DIR" "$ROOT"
else
  find "$ROOT" -mindepth 2 -maxdepth 2 -type d -name .git -print0 \
    | while IFS= read -r -d '' gitdir; do
        zoekt-git-index -index "$INDEX_DIR" "$(dirname "$gitdir")"
      done
fi
