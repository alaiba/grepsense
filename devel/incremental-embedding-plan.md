# Incremental semantic embedding — implementation plan

Status: **approved design, not yet implemented**
Decided: 2026-06-10 (discussion in optycode-workspace migration session)

## Problem

`grepsense embed` re-encodes the **entire** target tree on every run. The compose
`embedder` service loops it every `EMBED_INTERVAL` (default 3600 s), so a large
target (e.g. 25 repos / tens of thousands of files) pegs all CPU cores for the
full embed duration **every hour**, plus once per `docker compose up`. The
content-hash chunk IDs make storage idempotent (upserts don't duplicate), but the
expensive part — running the embedding model over every chunk — happens
unconditionally. There is also no pruning: vectors for deleted/renamed files
accumulate as orphans, and changed files leave stale chunks behind when their
line ranges shift.

The predecessor system (optycode `tools/code-index`, removed in commit `8db4986`
of optycode-workspace; readable via
`git show 8db4986~1:tools/code-index/embeddings/reindex_changed_files.py`) had a
full incremental subsystem that was **not ported** during extraction. This plan
ports its capabilities into grepsense, adapted to the container/stateless model.

## Feature parity inventory (old code-index → grepsense)

| Old feature | File (old) | Port? |
|---|---|---|
| Changed-file re-embed via git | `embeddings/reindex_changed_files.py` | ✅ this plan |
| Path-scoped embed (`--path`) | `embeddings/chunk_and_embed.py` | ✅ this plan (internal; CLI flag optional) |
| Stale-chunk deletion for changed/deleted paths | `delete_existing_chunks_for_paths` | ✅ this plan |
| Per-repo watermarks + HEAD tracking | status JSON on disk | ✅ this plan (state in Chroma instead) |
| Baseline of newly discovered repos | `run_once` baseline branch | ✅ this plan |
| Status/history observability | `*-status.json` / `*-history.jsonl` | ✅ this plan (`grepsense status` + state records) |
| Write lock (full vs incremental embed) | `semantic_lock.py` | ✅ this plan (file lock, same-host scope) |
| Zoekt incremental re-index | `-incremental` flag + loop | already in grepsense (compose `zoekt-indexer`) |

## Approach: hybrid (decided)

1. **Baseline:** first run per repo (no state record, or empty collection) →
   full embed of that repo (current behavior).
2. **Steady-state (git repos):** per repo, compute the changed set since the
   last successful pass using git:
   - `git log --since=<watermark> --name-only --diff-filter=ACMRTD` (committed
     changes since watermark),
   - `git diff --name-only <prev_head>..<HEAD>` when HEAD moved (catches
     rebases/pulls whose commit dates predate the watermark),
   - `git status --porcelain -z` (uncommitted changes; mtime-filtered against
     the watermark; both sides of renames included).
   Then **delete existing chunks** for all changed paths (and only re-embed the
   files that still exist). Update watermark + HEAD on success, per repo.
3. **Fallback (non-git trees):** content-hash skip — walk + chunk + hash, ask
   Chroma which IDs already exist (`collection.get(ids=...)` batched), encode
   only missing ones. (No deletion pruning possible without git; acceptable.)
4. Content-hash chunk IDs remain the idempotency floor under everything.

Cost profile after this: baseline once; steady-state cost is O(changed files),
so the hourly loop becomes near-free and `EMBED_INTERVAL` can stay at 3600 or
drop lower.

### Why git-diff and not only content-hash skip
Content-hash skip still walks/reads/chunks/hashes the whole tree every run
(O(repo size), even with zero changes) and cannot detect deletions. Git-diff is
O(changes) and yields the deletion set. The old system used git-diff *on top of*
content-hash IDs; we keep that layering.

## State: `_grepsense_state` collection in Chroma (decided: option B)

- One record per repo: `id = <repo name>`, metadata (or JSON document) =
  `{watermark: ISO-8601 UTC, head: <git sha>, last_run: {...summary...}}`.
  Records need a vector — use a constant 1-dim placeholder embedding.
- Per-repo upserts (no read-modify-write of shared state; safe interleaving).
- `grepsense embed --reset` drops **both** the data collection and the state
  collection (a reset must force re-baseline).
- No new volumes or bind mounts; Chroma remains the only stateful service.
- Rejected alternative (A): keys merged into the main collection's `metadata`
  dict — flat namespace, whole-dict read-modify-write races, conflates data and
  state lifecycles.

## Lock (decided: include)

Port the old `semantic_lock.py` file lock (flock on a lock file). Scope:
same-host/same-container protection — covers the realistic race (compose
embedder loop vs a manual `grepsense embed` in the same container). Lock file
lives in a temp/state dir inside the container (e.g.
`$XDG_RUNTIME_DIR/grepsense.lock` or `/tmp/grepsense-embed.lock`). Document that
cross-host writers are not protected (Chroma has no server-side lock; not worth
extra infra). Child invocations inherit via env (port `child_lock_env()`).

## Observability (decided: yes)

- Each repo pass writes a summary into its state record: started/completed,
  scope (baseline|incremental|fallback), files changed, chunks added/deleted,
  duration.
- New CLI: **`grepsense status`** — prints per-repo last-run table + totals
  (reads `_grepsense_state` + collection counts).
- `/readyz` may add embed freshness (age of newest watermark) as informational
  output; not a readiness gate.

## Code changes (files)

- `grepsense/state.py` (new): `_grepsense_state` read/upsert/reset helpers.
- `grepsense/gitchanges.py` (new): watermark/HEAD git logic — port of
  `changed_paths`, `committed_changes_since`, `diff_paths_between_heads`,
  `uncommitted_changes`, `current_head` from the old
  `reindex_changed_files.py`.
- `grepsense/lock.py` (new): port of `semantic_lock.py`.
- `grepsense/chunker.py` (refactor):
  - inject `encode_fn` and Chroma client/collection (constructor args or
    function params) instead of building them internally — **testability
    requirement**;
  - add `embed_paths(repo, paths)` (path-scoped embed) and
    `delete_chunks_for_paths(repo, paths)` (port of
    `delete_existing_chunks_for_paths`, batched 1000 ids);
  - add content-hash-skip mode for the non-git fallback.
- `grepsense/incremental.py` (new): orchestrator — per-repo baseline /
  incremental / fallback decision, state updates, lock acquisition; the
  function the loop calls.
- `grepsense/cli.py`: `embed` gains `--incremental` (default **on** for the
  loop; full embed still available via `--full`), add `status` command.
- `docker-compose.yml`: embedder loop calls the incremental entrypoint.
- Docs: README + docs/architecture.md sections on incremental behavior and
  state collection.

## Testing (decided)

### Unit (fast lane — runs in `ci.yml` on every push)
- Framework: `pytest`, new `tests/` dir; dev extra `[project.optional-dependencies] test = ["pytest", "chromadb"]`.
- **No torch:** embedding model is replaced by a stub `encode_fn` (constant
  vectors); imports stay lazy so `sentence-transformers` is never pulled in CI.
- Chroma: in-process `chromadb.EphemeralClient()` — no server.
- Git scenarios against `tmp_path` repos: commit/modify/delete/rename →
  changed-set and prune-set correctness; uncommitted changes; watermark
  respected; HEAD-jump (simulated rebase) caught; new repo → baseline.
- Pruning: change one file → exactly its old chunk IDs deleted; **assert
  `encode_fn` call count == chunks of changed files only** (the core
  regression guard for the CPU problem).
- State round-trip + `--reset` clears state; lock blocks a second runner.
- `ci.yml`: add `pip install .[test] && pytest` to the python job (replaces the
  `--no-deps` smoke; keep `grepsense version` check).

### E2E (release lane — decided: in `release.yml`, automated)
- New job in `release.yml` running **before** the image publish jobs (publish
  `needs:` it): build images locally in the runner, `docker compose up` against
  a small fixture repo, wait for `/readyz`, then via a python MCP client:
  - both tools return results;
  - touch/commit a file in the fixture, trigger an embed pass, assert the new
    content is findable and the pass was small (status output / encode count
    from logs).
- Keep it dockerized + scripted (`tests/e2e/run.sh`) so it can also run locally.

## Rollout

1. Implement + unit tests green locally and in CI.
2. Bump minor version (0.2.0 — behavior change of the embedder loop).
3. Tag → release.yml runs e2e gate → publishes multi-arch images.
4. Host stack: bump pinned tags in `~/Personal/grepsense/.env`, `docker compose
   pull && up -d`; verify with `grepsense status` that steady-state passes are
   near-instant.

## Non-goals (this iteration)

- Cross-host write locking / distributed coordination.
- Filesystem watch (inotify) instead of polling loop.
- Pluggable embedding providers (separate roadmap item).
- Deletion pruning for non-git trees.
