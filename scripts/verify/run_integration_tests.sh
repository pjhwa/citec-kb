#!/usr/bin/env bash
# G11 잔여 — 그래프 통합 테스트(GRAPH_TEST_DATABASE_URL/GRAPH_NEO4J_TEST_URI
# 가 없으면 skip되는 tests/test_graph_*_db.py 등)가 "환경 설정 안 함 = 조용히
# skip"으로 CI에서 영원히 안 돌아가는 걸 막는다. GRAPH_REQUIRE_INTEGRATION=1
# 이면 두 환경변수가 없는 것 자체를 실패로 본다 — "설정을 깜빡해서 스킵됐다"
# 와 "실제로 통과했다"를 구분한다(docs/features/graph-evidence.md G11 잔여).
#
# 파일마다 별도 pytest 프로세스로 돌린다 — 통합 테스트 파일들이 모듈
# 최상단에서 os.environ["DATABASE_URL"]을 설정해, 같은 pytest 세션에서
# 뒤에 도는 무관한 테스트(test_ops_dashboard_auth.py 등)에 그 값이 새는
# 기존 환경변수 오염 패턴이 있다(graph-evidence.md "검증 방법" 절 — 실제
# GitHub Actions CI는 이 두 env var를 안 설정해서 이 상호작용이 안 생겼을
# 뿐, 이 러너처럼 env var를 설정하고 같은 프로세스에서 전체 테스트를 돌리면
# 재현된다). 파일별 프로세스 분리로 이 오염을 구조적으로 피한다.
set -euo pipefail

cd "$(dirname "$0")/../../apps/api"

REQUIRE="${GRAPH_REQUIRE_INTEGRATION:-0}"
PYTHON="${GRAPH_VERIFY_PYTHON:-python}"

if [[ "$REQUIRE" == "1" ]] && [[ -z "${GRAPH_TEST_DATABASE_URL:-}" || -z "${GRAPH_NEO4J_TEST_URI:-}" ]]; then
  echo "✗ GRAPH_REQUIRE_INTEGRATION=1인데 GRAPH_TEST_DATABASE_URL/GRAPH_NEO4J_TEST_URI가 비어 있음 — 설정 누락을 skip이 아니라 실패로 처리" >&2
  exit 1
fi

if [[ -z "${GRAPH_TEST_DATABASE_URL:-}" || -z "${GRAPH_NEO4J_TEST_URI:-}" ]]; then
  echo "GRAPH_TEST_DATABASE_URL/GRAPH_NEO4J_TEST_URI 미설정 — 통합 테스트 전체 skip (GRAPH_REQUIRE_INTEGRATION=1이면 이 자체가 실패)"
  exit 0
fi

INTEGRATION_FILES=(
  tests/test_graph_pipeline_db.py
  tests/test_graph_neo4j_client.py
  tests/test_graph_hub_flags.py
  tests/test_graph_sync_state_db.py
  tests/test_graph_router.py
  tests/test_lexicon_seed_db.py
  tests/test_graph_sync_cli_db.py
  tests/test_ghost_node_audit_db.py
)

failed=0
for f in "${INTEGRATION_FILES[@]}"; do
  echo "──── $f ────"
  if ! "$PYTHON" -m pytest "$f" -q; then
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  echo "✗ 하나 이상의 그래프 통합 테스트 파일이 실패함" >&2
  exit 1
fi
echo "✓ 모든 그래프 통합 테스트 파일 통과"
