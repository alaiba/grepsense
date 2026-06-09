#!/usr/bin/env bash
#
# Index the mounted source tree into Zoekt.
#
# GREPSENSE_ROOT (default /code) is either a single git repo or a directory that
# contains git repos. Discovery is bash-native so this image needs no Python.
#
# Usage:
#   ./index-repos.sh                 # discover + index everything under GREPSENSE_ROOT
#   ./index-repos.sh /code/myrepo    # index explicit repo path(s)
set -euo pipefail

ROOT="${GREPSENSE_ROOT:-/code}"
INDEX_DIR="${ZOEKT_INDEX_DIR:-/data/index}"
FILE_LIMIT="${ZOEKT_FILE_LIMIT:-1048576}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IGNORE_FILE="${ZOEKT_IGNORE_FILE:-$SCRIPT_DIR/zoekt-ignore}"
ZOEKT_GIT_INDEX="${ZOEKT_GIT_INDEX:-zoekt-git-index}"
ZOEKT_INDEX="${ZOEKT_INDEX:-zoekt-index}"

mkdir -p "$INDEX_DIR"

# Discover repos from the on-disk git layout.
REPOS=()
if [ -d "$ROOT/.git" ]; then
  REPOS=("$ROOT")                       # ROOT is itself a repo
else
  for d in "$ROOT"/*/; do               # ROOT is a directory of repos
    [ -d "${d}.git" ] && REPOS+=("${d%/}")
  done
fi
# Explicit paths override discovery.
if [ "$#" -gt 0 ]; then REPOS=("$@"); fi

if [ "${#REPOS[@]}" -eq 0 ]; then
  echo "grepsense: no git repos found under $ROOT" >&2
  exit 1
fi

index_one() {
  local repo_path="$1"
  echo "INDEX: $repo_path"
  if [ -d "$repo_path/.git" ]; then
    local ignore=()
    [ -f "$IGNORE_FILE" ] && ignore=(-ignore_file "$IGNORE_FILE")
    "$ZOEKT_GIT_INDEX" \
      -index "$INDEX_DIR" \
      -file_limit "$FILE_LIMIT" \
      -branches HEAD \
      -incremental \
      "${ignore[@]}" \
      "$repo_path" || echo "  WARN: indexing $repo_path had errors (continuing)"
  else
    "$ZOEKT_INDEX" \
      -index "$INDEX_DIR" \
      -file_limit "$FILE_LIMIT" \
      -ignore_dirs ".git,node_modules,build,out,dist,.gradle,.nx,vendor,__pycache__" \
      "$repo_path" || echo "  WARN: indexing $repo_path had errors (continuing)"
  fi
}

for r in "${REPOS[@]}"; do index_one "$r"; done
echo "=== grepsense: indexed ${#REPOS[@]} repo(s) to $INDEX_DIR ==="
