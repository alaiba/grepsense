#!/bin/sh
#
# Apply all local Zoekt patches to the cloned source tree.
#
# Usage: apply-patches.sh <zoekt-source-dir>
#
# Called by the Dockerfile during image build. Each patch is a self-contained
# shell script under this directory, named 0001-*.sh, 0002-*.sh, etc.
# Patches are applied in lexicographic order.
#
set -eu

ZOEKT_SRC="${1:?usage: $0 <zoekt-source-dir>}"
PATCH_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Applying Zoekt patches from $PATCH_DIR ==="

for patch in "$PATCH_DIR"/[0-9][0-9][0-9][0-9]-*.sh; do
  [ -f "$patch" ] || continue
  echo "--- Applying: $(basename "$patch")"
  sh "$patch" "$ZOEKT_SRC"
done

echo "=== All patches applied ==="
