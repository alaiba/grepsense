# Incremental Semantic Embedding

> **Date:** 2026-06-10
> **Scope:** Port incremental embed + prune + state tracking from optycode `code-index` into grepsense so steady-state embed cost is O(changed files), not O(entire tree).
> **Primary sources:** `grepsense/chunker.py:121`, `grepsense/cli.py:35`, `docker-compose.yml:56`, `grepsense/discovery.py:15`, `grepsense/server.py:90`, `.github/workflows/ci.yml:9`, `.github/workflows/release.yml:14`
>
> **Related plans:**
> - None identified.

---

# Part I — Design

## 1. Goals

- Eliminate full-tree re-embedding on every embedder loop iteration; steady-state CPU cost should scale with changed files only.
- Restore deletion/stale-chunk pruning for git repos so vectors for removed or reshaped files do not accumulate as orphans.
- Persist per-repo watermarks and HEAD in Chroma (no new volumes) so container restarts resume incrementally without re-baselining.
- Expose operator visibility via `grepsense status` and per-run summaries in state records.
- Guard the hourly compose/Helm embed loop and manual `grepsense embed` from same-host races with a file lock.

---

## 2. Recommended Approach

**Hybrid incremental embed:** baseline full embed per repo on first sight; git-diff-driven incremental passes with path-scoped delete + re-embed for git repos; content-ID-skip fallback for non-git trees; positional chunk IDs remain the idempotency floor.

Rationale:
- Current `chunker.embed()` walks every file in every repo unconditionally (`grepsense/chunker.py:154-184`), and the compose embedder invokes it on startup and every `EMBED_INTERVAL` seconds (`docker-compose.yml:72-76`) — the CPU problem is encode volume, not Chroma upsert idempotency.
- Zoekt already uses `-incremental` re-indexing (`zoekt/index-repos.sh:50`); semantic search should match that cost profile.
- Git-diff is O(changes) and yields a deletion set; positional-ID skip alone still requires a full tree walk and cannot prune deletes (`devel/incremental-embedding-plan.md` discussion, predecessor `reindex_changed_files.py`).
- Chroma `_grepsense_state` collection (option B) avoids shared read-modify-write races and keeps data/state lifecycles separate.
- File lock scope matches realistic same-container races without distributed coordination overhead.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ embedder loop (compose / Helm)                                  │
│   grepsense embed [--incremental]  (default on in loop)       │
└────────────────────────────┬────────────────────────────────────┘
                             │ acquire flock (lock.py)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ incremental.run_once (incremental.py)                           │
│   for each repo from discovery.resolve_targets():                 │
│     read state record from _grepsense_state                     │
│     ├─ no state / --reset  → baseline (full repo embed)         │
│     ├─ .git present        → gitchanges → changed paths         │
│     │                        delete_chunks_for_paths            │
│     │                        embed_paths (existing files only)    │
│     └─ no .git             → fallback: walk + ID-skip encode    │
│     upsert state {watermark, head, last_run summary}            │
└────────────┬───────────────────────────────┬────────────────────┘
             │                               │
             ▼                               ▼
   grepsense collection              _grepsense_state collection
   (chunk vectors)                   (1-dim placeholder vectors)
             ▲
             │ encode_fn (injectable; stub in tests)
             │
      chunker.embed_paths / chunk_file
```

**Data flow (git incremental pass):**
1. Load per-repo state: `{watermark, head, last_run}` from `_grepsense_state` (`id = <repo name>`).
2. Compute changed paths: `git log --since=<watermark>`, `git diff <prev_head>..HEAD`, `git status --porcelain` (mtime-filtered).
3. `delete_chunks_for_paths(repo, changed_paths)` — batched `collection.delete(ids=...)` (port of `delete_existing_chunks_for_paths`).
4. `embed_paths(repo, existing_paths)` — chunk + encode only those files.
5. On success, upsert state with new watermark (UTC ISO-8601), HEAD sha, and run summary.

**CLI / observability:**
- `grepsense status` reads `_grepsense_state` + main collection counts.

---

## 4. Non-Negotiable Constraints

1. Content-hash / positional chunk IDs remain the idempotency floor — do not change the ID formula in `grepsense/chunker.py:166-168` without a migration plan. *(Source: approved design discussion 2026-06-10)*
2. State lives in Chroma `_grepsense_state` collection with per-repo upserts; no new volumes or bind mounts. *(Source: approved design)*
3. `grepsense embed --reset` drops **both** the data collection and `_grepsense_state`. *(Source: approved design)*
4. File lock is same-host/same-container only; cross-host writers are explicitly out of scope. *(Source: approved design)*
5. Unit tests must not import `sentence-transformers` / torch; use injectable `encode_fn` stub. *(Source: approved design)*
6. E2E gate runs in `release.yml` before image publish; publish jobs `needs:` it. *(Source: approved design)*
7. Zoekt incremental behavior stays as-is; this plan does not change `zoekt/index-repos.sh`. *(Source: feature parity table)*

---

## 5. Verified Current State

### 5.1 Full-tree embed on every run

`grepsense/chunker.embed()` resolves all repos, walks every matching file, chunks, and encodes via `_flush()` on each batch (`grepsense/chunker.py:121-191`). There is no changed-file detection, path-scoped embed, or chunk deletion. Chroma client and `SentenceTransformer` are constructed inside `embed()` (`grepsense/chunker.py:139-148`), which blocks unit testing without heavy ML deps.

### 5.2 Embedder loop invokes full embed unconditionally

The compose `embedder` service waits for Chroma heartbeat, runs `grepsense embed --root /code`, then loops `sleep EMBED_INTERVAL` + full embed (`docker-compose.yml:68-77`). Default interval is 3600 s (`docker-compose.yml:65`). The Helm embedder deployment mirrors this (`charts/grepsense/templates/deployments.yaml:272-275`).

### 5.3 CLI surface

`grepsense embed` accepts `--root`, `--repo`, `--reset` only (`grepsense/cli.py:35-38`). No `status` command. No `--incremental` / `--full` flags.

### 5.4 Chunk IDs and metadata

Chunk IDs are SHA-256 truncations of `{repo}:{rel_path}:{start_line}:{end_line}:{chunk_index}` (`grepsense/chunker.py:166-168`). Metadata stores `repo`, `file_path`, line range, `language` (`grepsense/chunker.py:171-177`). Upserts are idempotent for unchanged line ranges; changed content at the same lines reuses IDs (hence delete-before-re-embed for changed paths).

### 5.5 Repository discovery

`discovery.resolve_targets()` returns `(effective_root, repo_names)` for a git root or child git repos (`grepsense/discovery.py:15-28`). Embed raises if no git repos found (`grepsense/chunker.py:132-133`) — non-git trees are not embedded today.

### 5.6 No incremental state or lock modules

No `grepsense/state.py`, `gitchanges.py`, `lock.py`, or `incremental.py` exist. No `_grepsense_state` collection handling.

### 5.7 CI and release pipelines

CI python job: `pip install --no-deps .`, `grepsense version`, `compileall` only (`.github/workflows/ci.yml:18-20`). `test` optional dep includes `pytest` only (`pyproject.toml:33-35`). One test module: `tests/test_health.py` (MCP `/healthz` + `/readyz`). Release workflow builds and publishes multi-arch images on tag with no e2e gate (`.github/workflows/release.yml:14-79`).

### 5.8 Zoekt incremental parity (lexical layer)

Zoekt git indexing already passes `-incremental` (`zoekt/index-repos.sh:46-51`). Semantic layer lacks equivalent.

### 5.9 Docker image includes git

The grepsense image installs `git` via apt (`Dockerfile:6-8`), satisfying subprocess requirements for `gitchanges.py`.

### 5.10 Predecessor system (external reference)

optycode `tools/code-index/embeddings/reindex_changed_files.py` (removed in commit `8db4986` of optycode-workspace) implemented changed-file re-embed, watermark/HEAD tracking, stale-chunk deletion, and `semantic_lock.py`. Capabilities were not ported during grepsense extraction.

---

## 6. Prerequisites

1. **ChromaDB reachable from embedder** — already required by compose (`docker-compose.yml:69-71`); owner: existing stack.
2. **Git available in embedder container** — target repos are git repos; embedder mounts source read-only (`docker-compose.yml:79`); owner: existing image (verify `git` binary in Dockerfile if gitchanges shells out).
3. **`pytest` + `chromadb` in `[project.optional-dependencies] test`** — owner: this plan Phase 1.
4. **Git in embedder image** — already installed (`Dockerfile:7`); owner: existing image.
5. **Predecessor reference** — `git show 8db4986~1:tools/code-index/embeddings/reindex_changed_files.py` from optycode-workspace for porting git logic; owner: implementer.

---

## 7. Out of Scope

- Cross-host write locking / distributed coordination.
- Filesystem watch (inotify) instead of polling loop.
- Pluggable embedding providers.
- Deletion pruning for non-git trees (fallback is encode-skip only).
- Changing chunk ID formula or embedding model.
- PyPI publish job (deferred per `release.yml:81-82`).

---

# Part II — Implementation

## 8. Phased Plan

### Phase 1. Test harness and chunker dependency injection

Objective: Enable fast unit tests without torch and establish the embed primitive API incremental work builds on.

Planned work:

1. Add `chromadb` to `[project.optional-dependencies] test` in `pyproject.toml:33-35`.
2. Refactor `grepsense/chunker.py` so `_flush()` and new helpers accept injected `collection` and `encode_fn` (signature: `encode_fn(docs: list[str]) -> list[list[float]]`) instead of always calling `semantic.load_model()` (`grepsense/chunker.py:105-118`, `148`).
3. Extract shared batch upsert logic; keep existing `embed()` as a thin wrapper that builds client/model and delegates (backward compatible for manual full embed).
4. Add `tests/conftest.py` with `EphemeralClient` fixture and stub `encode_fn` returning constant vectors.
5. Update `.github/workflows/ci.yml:18-20` to `pip install .[test] && pytest` while keeping `grepsense version` smoke check.

Files expected:
- `pyproject.toml` modified
- `grepsense/chunker.py` modified
- `tests/conftest.py` new
- `.github/workflows/ci.yml` modified

Acceptance criteria:
- `pytest` passes locally and in CI without importing `sentence_transformers`.
- Existing `chunker.embed()` CLI path still performs a full embed against a running Chroma.

---

### Phase 2. State collection and file lock

Objective: Persist per-repo watermarks safely and prevent concurrent embed passes on the same host.

Planned work:

1. Create `grepsense/state.py`: constants `STATE_COLLECTION = "_grepsense_state"`, `PLACEHOLDER_EMBEDDING = [0.0]`; helpers `get_state_client`, `get_state_record`, `upsert_state`, `delete_all_state`, integrated with `Config.collection` for the data collection name.
2. Store JSON-serialized metadata: `{watermark, head, last_run: {started, completed, scope, files_changed, chunks_added, chunks_deleted, duration_s}}`.
3. Create `grepsense/lock.py`: port flock-based lock from optycode `semantic_lock.py`; lock path `$XDG_RUNTIME_DIR/grepsense-embed.lock` with fallback `/tmp/grepsense-embed.lock`; export `child_lock_env()` for subprocess inheritance.
4. Wire `reset=True` in embed path to delete both `config.collection` and `STATE_COLLECTION` (`grepsense/chunker.py:140-144` extension point).
5. Unit tests: state round-trip via `EphemeralClient`; lock blocks second acquirer; `--reset` clears state collection.

Files expected:
- `grepsense/state.py` new
- `grepsense/lock.py` new
- `tests/test_state.py` new
- `tests/test_lock.py` new

Acceptance criteria:
- Per-repo upsert/read works without read-modify-write of other repos' records.
- Second embed process blocks or skips (document exact behavior in lock module) when lock held.

---

### Phase 3. Git change detection

Objective: Compute the changed-path set for incremental git repos.

Planned work:

1. Create `grepsense/gitchanges.py` porting from `reindex_changed_files.py`: `current_head`, `committed_changes_since(watermark)`, `diff_paths_between_heads(prev, head)`, `uncommitted_changes(watermark)`, `changed_paths(repo_path, state) -> set[str]`.
2. Union committed-since-watermark, HEAD-jump diff (rebase/pull), and mtime-filtered uncommitted porcelain paths (both sides of renames).
3. Unit tests in `tests/test_gitchanges.py` using `tmp_path` git repos: commit/modify/delete/rename, dirty working tree, watermark respected, simulated rebase (HEAD jump with old commit dates), empty changed set.

Files expected:
- `grepsense/gitchanges.py` new
- `tests/test_gitchanges.py` new

Acceptance criteria:
- Each git scenario returns the expected path set.
- HEAD-jump case includes files not caught by `--since` alone.

---

### Phase 4. Path-scoped embed, prune, and non-git fallback

Objective: Encode only needed chunks and delete stale vectors for changed paths.

Planned work:

1. Add `delete_chunks_for_paths(collection, repo, paths, *, batch_size=1000)` — query/delete by `where={"repo": repo, "file_path": {"$in": batch}}` or get-by-metadata pattern supported by Chroma v1 API; batched deletes.
2. Add `embed_paths(collection, encode_fn, config, repo, paths)` — `collect_files` filtered to `paths`, chunk, build IDs via existing formula (`grepsense/chunker.py:166-177`), batch upsert.
3. Add `embed_repo_fallback_skip(collection, encode_fn, config, repo)` — full walk; batch `collection.get(ids=...)` to skip existing IDs; encode missing only (no delete pruning).
4. Regression test: modify one file in fixture repo → assert `encode_fn` call count equals chunks of that file only; assert old chunk IDs for that file are deleted before re-embed.

Files expected:
- `grepsense/chunker.py` modified
- `tests/test_chunker_incremental.py` new

Acceptance criteria:
- Changing one file triggers encode calls proportional to that file's chunk count, not whole repo.
- Deleted file paths result in zero remaining chunks for that `file_path` in Chroma.

---

### Phase 5. Incremental orchestrator and CLI

Objective: Wire per-repo baseline/incremental/fallback decision tree and expose operator commands.

Planned work:

1. Create `grepsense/incremental.py` with `run_once(config, *, repo_filter, reset, incremental=True) -> dict` — acquire lock, iterate repos, branch on state presence and `.git`, call phase 4 primitives, upsert state on success.
2. Update `grepsense/cli.py:35-67`: `embed` calls `incremental.run_once`; add `--incremental` (default `True`), `--full` (sets `incremental=False`); add `status` subcommand printing per-repo table from `_grepsense_state` + collection counts.
3. Update `docker-compose.yml:72-76` embedder command to use incremental embed (explicit `--incremental` or rely on default).
4. Update `charts/grepsense/templates/deployments.yaml:272-275` embedder loop identically.

Files expected:
- `grepsense/incremental.py` new
- `grepsense/cli.py` modified
- `docker-compose.yml` modified
- `charts/grepsense/templates/deployments.yaml` modified
- `tests/test_incremental.py` new
- `tests/test_cli.py` new

Acceptance criteria:
- First run on fresh Chroma performs baseline (full) embed per repo; state records created.
- Second run with no changes encodes zero documents (stub test) and completes quickly.
- `grepsense status` prints per-repo scope, timestamps, and chunk deltas from last run.
- `grepsense embed --reset` clears both collections.

---

### Phase 6. Documentation and version bump

Objective: Document new behavior and signal the behavior change release.

Planned work:

1. Update `docs/architecture.md:30-35` Layer 2 section to describe incremental embed, state collection, and lock semantics.
2. Update `README.md` embed/status sections and env var table if new vars added.
3. Bump `pyproject.toml:7` and `grepsense/__init__.py:7` to `0.2.0`.

Files expected:
- `docs/architecture.md` modified
- `README.md` modified
- `pyproject.toml` modified
- `grepsense/__init__.py` modified

Acceptance criteria:
- Docs describe baseline vs incremental vs fallback, `--reset`, and `grepsense status`.
- Version is `0.2.0`.

---

### Phase 7. E2E release gate

Objective: Automated dockerized smoke test before image publish.

Planned work:

1. Add `tests/e2e/fixture/` — small git repo fixture.
2. Add `tests/e2e/run.sh` — build images, `docker compose up`, wait for `/readyz`, exercise MCP tools, touch+commit file, trigger embed, assert semantic findability and small pass (status output or log marker).
3. Add `e2e` job to `.github/workflows/release.yml` before `build` job; set `build.needs: [e2e]` and `merge.needs: [build]` (adjust matrix merge accordingly).
4. Document local invocation: `tests/e2e/run.sh`.

Files expected:
- `tests/e2e/run.sh` new
- `tests/e2e/fixture/` new
- `.github/workflows/release.yml` modified

Acceptance criteria:
- Tagged release fails if e2e fails; passes on green run.
- `tests/e2e/run.sh` succeeds locally with Docker available.

---

## 9. Validation Plan

1. **Unit (CI):** `pip install .[test] && pytest` — all gitchanges, chunker, state, lock, incremental, CLI tests green without torch.
2. **Manual full baseline:** Fresh volume, `docker compose up -d`; `grepsense status` shows `baseline` scope per repo; collection count matches file count order-of-magnitude.
3. **Manual incremental:** `touch` + `git commit` one file in a mounted repo; wait for loop or run `grepsense embed`; `status` shows `incremental`, small `files_changed`; semantic search finds new content.
4. **Prune check:** Delete a file, commit; after embed pass, `semantic_code_search` no longer returns chunks for that path; Chroma metadata query confirms zero chunks for deleted `file_path`.
5. **Reset:** `grepsense embed --reset`; both collections empty; next pass re-baselines.
6. **Lock:** In running embedder container, start second `grepsense embed` — second runner blocks or exits cleanly per lock design (no duplicate concurrent writes).
7. **Release e2e:** Tag `v0.2.0`; `release.yml` e2e job passes before images publish.
8. **Production rollout:** Bump pinned image tag in host `.env`; `docker compose pull && up -d`; verify steady-state hourly passes complete in seconds via `grepsense status`.

---

## 10. Implementation Order

1. **Phase 1** — Test harness + injection; unblocks all other unit work.
2. **Phase 2** — State + lock; no git logic yet, independently testable.
3. **Phase 3** — Git change detection; pure functions, no Chroma.
4. **Phase 4** — Path embed/prune/skip; depends on Phase 1 injection, testable without orchestrator.
5. **Phase 5** — Orchestrator + CLI + compose/Helm; integrates 2–4.
6. **Phase 6** — Docs + version; after behavior stabilizes.
7. **Phase 7** — E2E gate; after full stack wiring, before release tag.

---

# Part III — Review

## 11. Dependencies & Integration Points

| Dependency | Owner | Status | Notes |
|---|---|---|---|
| ChromaDB persistent volume | compose / Helm | ready | `chromadb/chroma` + `chroma-data` volume (`docker-compose.yml:47-54`) |
| Git binary in grepsense image | Dockerfile | ready | Installed at `Dockerfile:7` |
| optycode `reindex_changed_files.py` reference | external repo | ready | `git show 8db4986~1:...` |
| `chromadb` in test extras | this plan Phase 1 | blocked | Not in `pyproject.toml` today |
| Helm embedder loop | this plan Phase 5 | blocked | Still full embed (`charts/grepsense/templates/deployments.yaml:272-275`) |
| MCP `/readyz` tests | existing | ready | `tests/test_health.py`; unchanged by this plan |
| Release e2e runner | this plan Phase 7 | blocked | No e2e job in `release.yml` |

---

## 12. Decision Log

| ID | Decision | Alternatives considered | Rationale | Date |
|---|---|---|---|---|
| D1 | Hybrid: git-diff incremental + ID-skip fallback | Content-ID skip only; always full embed | Git-diff is O(changes) and enables deletion detection; skip-only still walks full tree | 2026-06-10 |
| D2 | State in `_grepsense_state` Chroma collection | Metadata keys in main collection; SQLite sidecar | Per-repo upserts, no new volumes, separates lifecycles | 2026-06-10 |
| D3 | flock file lock, same-host scope | No lock; distributed lock | Covers compose loop vs manual embed race; cross-host out of scope | 2026-06-10 |
| D4 | `--incremental` default on; `--full` override | Opt-in incremental | Embedder loop should be cheap by default | 2026-06-10 |
| D5 | Injectable `encode_fn` for tests | Mock `SentenceTransformer` | Keeps CI free of torch; explicit regression on call count | 2026-06-10 |
| D6 | E2E in `release.yml` not `ci.yml` | E2E on every push; manual only | Docker build cost; gate releases | 2026-06-10 |
| D7 | Bump minor to 0.2.0 | Patch 0.1.x | Embedder loop behavior change | 2026-06-10 |
| D8 | Update Helm chart embedder alongside compose | Compose only | Helm deployment duplicates embed loop | 2026-06-10 |

---

## 13. Findings

### F1: Helm embedder loop omitted from original file list
<!-- severity: major -->
<!-- dimension: gaps -->
<!-- status: Applied -->

**Context:** Original plan listed `docker-compose.yml` but not `charts/grepsense/templates/deployments.yaml:272-275`, which runs the same full-embed loop.

**Issue:** Shipping compose-only changes would leave Kubernetes deployments re-embedding the full tree hourly.

**Recommendation:** Include Helm embedder command update in Phase 5; record D8.

**Choices:**
- [x] Update Helm template in Phase 5 alongside compose
- [ ] Document Helm as manual follow-up

### F2: Non-git repo fallback vs current embed guard
<!-- severity: minor -->
<!-- dimension: correctness -->
<!-- status: Applied -->

**Context:** Plan describes non-git fallback (`ID-skip`), but `chunker.embed()` raises when no git repos exist (`grepsense/chunker.py:132-133`). Discovery only returns git repos (`grepsense/discovery.py:18-28`).

**Issue:** Fallback path is unreachable with current discovery unless discovery is extended.

**Recommendation:** Implement fallback in `incremental.py` for repos that are directories without `.git` if explicit `--repo` or future discovery expands; for v0.2.0, incremental orchestrator can skip non-git children. Document in Phase 5 that fallback is defensive for explicit paths, not multi-repo discovery.

**Choices:**
- [x] Implement fallback as defensive branch; do not change discovery in v0.2.0
- [ ] Extend discovery to non-git dirs in v0.2.0

### F3: `test` extra missing `chromadb`
<!-- severity: minor -->
<!-- dimension: prerequisites -->
<!-- status: Applied -->

**Context:** Plan requires `EphemeralClient` tests; `pyproject.toml:33-35` lists only `pytest`.

**Issue:** CI `pip install .[test]` would not install chromadb for in-process tests.

**Recommendation:** Phase 1 adds `chromadb>=1.0` to test extras (already a main dep at `pyproject.toml:27`).

**Choices:**
- [x] Add chromadb to test extra in Phase 1
- [ ] Rely on main deps in CI (`pip install .[test]` with full install)

### F4: Chunk ID naming vs "content-hash"
<!-- severity: minor -->
<!-- dimension: plan-hygiene -->
<!-- status: Applied -->

**Context:** Design discussion referred to "content-hash chunk IDs"; actual IDs hash positional coordinates (`grepsense/chunker.py:166-168`), not file bytes.

**Issue:** Mislabeling could mislead implementers into content-based IDs (breaking compatibility).

**Recommendation:** Verified state and constraints use "positional chunk IDs"; delete-before-re-embed handles content changes at same lines.

**Choices:**
- [x] Use accurate "positional ID" terminology in plan
- [ ] Migrate to content-hash IDs (out of scope)

### F5: Git binary in container image unverified
<!-- severity: minor -->
<!-- dimension: gaps -->
<!-- status: Applied -->

**Context:** Phase 3 shells out to git; Dockerfile not cited in original plan.

**Issue:** Missing git would break incremental mode at runtime.

**Recommendation:** Verified `git` is installed in `Dockerfile:7`; cite in §5.9 and prerequisites. No image change needed.

**Choices:**
- [x] Verified present — document only
- [ ] Add git to Dockerfile

### Findings summary

| # | Title | Severity | Dimension | Depends on |
|---|-------|----------|-----------|------------|
| F1 | Helm embedder loop omitted | major | gaps | — |
| F2 | Non-git fallback vs discovery | minor | correctness | — |
| F3 | test extra missing chromadb | minor | prerequisites | — |
| F4 | Chunk ID naming | minor | plan-hygiene | — |
| F5 | Git binary in image | minor | gaps | — |

---

## 14. Plan Reviews

| Date | Reviewer | Scope | Outcome |
|---|---|---|---|
| 2026-06-10 | AI agent | Initial creation from approved design draft | Converted informal plan to three-part structure with code citations |
| 2026-06-10 | AI agent | Full sweep (testing, correctness, gaps, best-practices, plan-hygiene) | F1–F5 found; F1 major applied (Helm added to Phase 5, D8); zero remaining critical/major after apply |
