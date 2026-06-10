#!/usr/bin/env bash
# End-to-end smoke test: build stack, baseline embed, incremental update, semantic hit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

FIXTURE_ROOT="$ROOT/tests/e2e/fixture"
REPO="$FIXTURE_ROOT/demo-repo"

if [ ! -d "$REPO/.git" ]; then
  git -C "$REPO" init -b main
  git -C "$REPO" config user.email "e2e@grepsense.test"
  git -C "$REPO" config user.name "grepsense e2e"
  git -C "$REPO" add .
  git -C "$REPO" commit -m "init"
fi

export GREPSENSE_TARGET="$FIXTURE_ROOT"
export GREPSENSE_COLLECTION="grepsense-e2e"
export GREPSENSE_HTTP_PORT="${GREPSENSE_HTTP_PORT:-18765}"
export CHROMADB_VOLUME="grepsense-e2e-chroma-${RANDOM}"
export ZOEKT_VOLUME="grepsense-e2e-zoekt-${RANDOM}"
export COMPOSE_PROJECT_NAME="grepsense-e2e-${RANDOM}"

cleanup() {
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== grepsense e2e: building images ==="
docker compose build

echo "=== grepsense e2e: starting stack ==="
docker compose up -d

echo "=== grepsense e2e: waiting for /readyz ==="
ready=0
for _ in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:${GREPSENSE_HTTP_PORT}/readyz" | grep -q '"ready"'; then
    ready=1
    break
  fi
  sleep 5
done
if [ "$ready" -ne 1 ]; then
  echo "grepsense e2e: /readyz never became ready" >&2
  docker compose logs
  exit 1
fi

echo "=== grepsense e2e: waiting for baseline embed ==="
baseline=0
for _ in $(seq 1 120); do
  if docker compose exec -T embedder grepsense status --root /code 2>/dev/null | grep -q baseline; then
    baseline=1
    break
  fi
  sleep 5
done
if [ "$baseline" -ne 1 ]; then
  echo "grepsense e2e: baseline embed did not complete" >&2
  docker compose logs embedder
  exit 1
fi

echo "=== grepsense e2e: zoekt lexical search ==="
docker compose exec -T mcp python -c "
import httpx, sys
from grepsense.config import Config
cfg = Config.load()
r = httpx.get(cfg.zoekt_search_url, params={'q': 'xyzzyunique123', 'num': 5, 'format': 'json'}, timeout=30)
r.raise_for_status()
if not (r.json().get('result') or {}).get('FileMatches'):
    sys.exit('zoekt: no matches')
print('zoekt: ok')
"

echo "=== grepsense e2e: semantic search after baseline ==="
docker compose exec -T embedder python -c "
from grepsense.config import Config
from grepsense import semantic
cfg = Config.load()
hits = semantic.search('skylark authentication xyzzyunique123', host=cfg.chroma_host, port=cfg.chroma_port, collection=cfg.collection, model_name=cfg.embedding_model)
assert hits, 'semantic: no matches after baseline'
print('semantic: ok')
"

echo "=== grepsense e2e: incremental pass after commit ==="
echo "# e2e touch" >> "$REPO/hello.py"
git -C "$REPO" add hello.py
git -C "$REPO" commit -m "e2e incremental"

docker compose exec -T embedder grepsense embed --root /code
status="$(docker compose exec -T embedder grepsense status --root /code)"
echo "$status"
echo "$status" | grep -q incremental

echo "=== grepsense e2e: PASSED ==="
