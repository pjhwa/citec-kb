#!/usr/bin/env bash
# G05 — generation 마커 발급/재발급/skip 동작을 scratch Neo4j+Postgres에서 검증.
# 필수: GRAPH_TEST_DATABASE_URL, GRAPH_NEO4J_TEST_URI (및 USER/PASSWORD).
set -euo pipefail

: "${GRAPH_TEST_DATABASE_URL:?set to a scratch Postgres DSN}"
: "${GRAPH_NEO4J_TEST_URI:?set to a scratch Neo4j bolt URI}"
NEO4J_USER="${GRAPH_NEO4J_TEST_USER:-neo4j}"
NEO4J_PASS="${GRAPH_NEO4J_TEST_PASSWORD:?set to the scratch Neo4j password}"

cd "$(dirname "${BASH_SOURCE[0]}")/../../apps/api"
LOG=/tmp/graph-verify-generation.log
: > "$LOG"

export DATABASE_URL="$GRAPH_TEST_DATABASE_URL"
export NEO4J_URI="$GRAPH_NEO4J_TEST_URI"
export NEO4J_USERNAME="$NEO4J_USER"
export NEO4J_PASSWORD="$NEO4J_PASS"
PY=/tmp/graph-ci-venv/bin/python
[ -x "$PY" ] || PY=python3

echo "=== run 1: no marker yet -> mint + sync ===" | tee -a "$LOG"
$PY -m app.graph.sync_cli 2>&1 | tee -a "$LOG"

echo "=== run 2: same marker -> all skip ===" | tee -a "$LOG"
$PY -m app.graph.sync_cli 2>&1 | tee -a "$LOG"

echo "=== delete marker, run 3: new mint -> full resync again ===" | tee -a "$LOG"
$PY -c "
from app.graph.neo4j_client import Neo4jClient
c = Neo4jClient()
with c._driver.session() as s:
    s.run(\"MATCH (m:GraphMeta {key:'generation'}) DELETE m\")
c.close()
" 2>&1 | tee -a "$LOG"
$PY -m app.graph.sync_cli 2>&1 | tee -a "$LOG"

echo "Full output saved to $LOG"
echo "Acceptance: run1 synced>0/skipped=0; run2 synced=0/skipped=run1.synced; run3 synced>0 again (new mint != run1 mint)."
