#!/bin/sh
#
# Patch: add -ignore_file flag to zoekt-git-index
#
# Adds a CLI flag that accepts an external ignore-patterns file (same format
# as .sourcegraph/ignore). When specified, patterns from the file are merged
# with any in-tree .sourcegraph/ignore. When not specified, behavior is
# unchanged.
#
# Pinned to sourcegraph/zoekt commit 071adfde901e3e10992ec5afc11f54984ab7e7fc.
#
set -eu

ZOEKT_SRC="${1:?usage: $0 <zoekt-source-dir>}"

GITINDEX="$ZOEKT_SRC/gitindex/index.go"
TREE_GO="$ZOEKT_SRC/gitindex/tree.go"
MAIN_GO="$ZOEKT_SRC/cmd/zoekt-git-index/main.go"

# --- 1. Add IgnoreFile field to gitindex.Options struct ---

if grep -q 'IgnoreFile string' "$GITINDEX"; then
  echo "  [skip] IgnoreFile field already present"
else
  sed -i '/DeltaShardNumberFallbackThreshold uint64/a\
\
\t// IgnoreFile is an optional path to an external file containing\
\t// ignore patterns (same format as .sourcegraph/ignore). When set,\
\t// patterns from this file are merged with any in-tree ignore file.\
\tIgnoreFile string' "$GITINDEX"
  echo "  [done] Added IgnoreFile field to gitindex.Options"
fi

# --- 2. Replace newIgnoreMatcher to accept externalIgnoreFile param ---

if grep -q 'func newIgnoreMatcher(tree \*object.Tree, externalIgnoreFile string)' "$GITINDEX"; then
  echo "  [skip] newIgnoreMatcher already patched"
else
  cat > /tmp/new_ignore_matcher.go <<'GOFUNC'
func newIgnoreMatcher(tree *object.Tree, externalIgnoreFile string) (*ignore.Matcher, error) {
	var parts []string

	// Read in-tree .sourcegraph/ignore if present.
	if f, err := tree.File(ignore.IgnoreFile); err == nil {
		content, err := f.Contents()
		if err != nil {
			return nil, err
		}
		parts = append(parts, content)
	} else if err != object.ErrFileNotFound {
		return nil, err
	}

	// Read external ignore file if specified.
	if externalIgnoreFile != "" {
		data, err := os.ReadFile(externalIgnoreFile)
		if err != nil {
			return nil, fmt.Errorf("reading ignore file %q: %w", externalIgnoreFile, err)
		}
		parts = append(parts, string(data))
	}

	if len(parts) == 0 {
		return &ignore.Matcher{}, nil
	}

	combined := strings.Join(parts, "\n")
	return ignore.ParseIgnoreFile(strings.NewReader(combined))
}
GOFUNC

  awk '
    /^func newIgnoreMatcher\(tree \*object\.Tree\)/ {
      while ($0 !~ /^}$/) { getline }
      while ((getline line < "/tmp/new_ignore_matcher.go") > 0) { print line }
      next
    }
    { print }
  ' "$GITINDEX" > "$GITINDEX.tmp" && mv "$GITINDEX.tmp" "$GITINDEX"
  echo "  [done] Replaced newIgnoreMatcher function"
fi

# --- 3. Update call sites in index.go ---
# In prepareNormalBuildRecurse the parameter is named "options".

if grep -q 'newIgnoreMatcher(tree, options.IgnoreFile)' "$GITINDEX"; then
  echo "  [skip] index.go call sites already updated"
else
  sed -i 's/newIgnoreMatcher(tree)/newIgnoreMatcher(tree, options.IgnoreFile)/g' "$GITINDEX"
  echo "  [done] Updated newIgnoreMatcher call sites in index.go"
fi

# --- 4. Update call site in tree.go ---
# CollectFiles does not have access to options; pass "" for submodule trees
# (the external ignore file only applies at the top-level repo).

if grep -q 'newIgnoreMatcher(t, "")' "$TREE_GO"; then
  echo "  [skip] tree.go call site already updated"
else
  sed -i 's/newIgnoreMatcher(t)/newIgnoreMatcher(t, "")/g' "$TREE_GO"
  echo "  [done] Updated newIgnoreMatcher call site in tree.go"
fi

# --- 5. Add -ignore_file flag to cmd/zoekt-git-index/main.go ---

if grep -q 'ignore_file' "$MAIN_GO"; then
  echo "  [skip] -ignore_file flag already present"
else
  sed -i '/incremental := flag.Bool("incremental"/a\
\tignoreFile := flag.String("ignore_file", "", "path to an external ignore-patterns file (same format as .sourcegraph/ignore)")' "$MAIN_GO"
  echo "  [done] Added -ignore_file flag declaration"
fi

# --- 6. Wire IgnoreFile into gitOpts struct ---

if grep -q 'IgnoreFile:.*\*ignoreFile' "$MAIN_GO"; then
  echo "  [skip] IgnoreFile already wired into gitOpts"
else
  sed -i '/DeltaShardNumberFallbackThreshold: \*deltaShardNumberFallbackThreshold,/a\
\t\t\tIgnoreFile:                        *ignoreFile,' "$MAIN_GO"
  echo "  [done] Added IgnoreFile to gitOpts struct literal"
fi

echo "  patch 0001: complete"
