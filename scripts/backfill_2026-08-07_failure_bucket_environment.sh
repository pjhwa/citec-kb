#!/usr/bin/env bash
# backfill_2026-08-07_failure_bucket_environment.sh
#
# One-off content backfill: sets `failure_buckets.environment` for the 3 of the
# 4 legacy (pre-multi-plugin, evidence_ref='legacy:pre-migration') network
# failure_buckets whose own recorded symptom/root_cause text gives enough
# evidence to tag an environment, then re-syncs the Document mirror so
# kb_search(environment=...) can find them too (see 20260807 commits and
# docs/../packet-capture-rca_개선지침_및_citec-kb_연동분석.md §B-1 실행 결과 / §B-1d).
#
# This is NOT part of the alembic migration (20260807_0005 intentionally adds
# the column with no data change — see that file's comment) because these are
# specific, judgment-based values for 3 particular rows, not a schema-level
# requirement. Run this once per environment that has these legacy rows
# (dev already has it applied; run on 운영 after deploying the code bundle
# that includes the 20260807_0005 migration + the environment-aware
# service.py/draft.py).
#
# Idempotent:
#   - the UPDATEs only touch rows where environment IS NULL (never overwrites
#     a value already set through the API, e.g. via kb_refine_failure_bucket)
#   - the reindex step only re-syncs rows where the Document mirror disagrees
#     with failure_buckets.environment
#   - matched by bucket_name (stable, human-reviewable) rather than a
#     hardcoded UUID, since row ids are instance-specific
#
# Usage: scripts/backfill_2026-08-07_failure_bucket_environment.sh [--project DIR]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--project DIR]"
      exit 0
      ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

cd "$PROJECT_DIR"

echo "[1/2] failure_buckets.environment 백필 (NULL인 경우에만, bucket_name 매칭)"
docker compose exec -T postgres psql -U citec -d citec_knowledge -v ON_ERROR_STOP=1 <<'SQL'
UPDATE failure_buckets SET environment = 'onprem', updated_at = now()
  WHERE bucket_name = '경로상 ECN 오처리 장비로 인한 양방향 실유실+RTT 팽창 (ECN 비활성화는 부분완화)'
    AND environment IS NULL;

UPDATE failure_buckets SET environment = 'onprem', updated_at = now()
  WHERE bucket_name = 'WAN 구간 흐름-선택적 손실(레이트 폴리서/tail-drop)로 대형 지속 업로드만 선택적 저하'
    AND environment IS NULL;

UPDATE failure_buckets SET environment = 'hybrid', updated_at = now()
  WHERE bucket_name = 'LB 프론트 수신버퍼 포화 → chunked 업로드 본문 미완결 조기 종료'
    AND environment IS NULL;

-- '클라이언트 VM 동시 프로세스 경합...' 버킷은 의도적으로 채우지 않음
-- (CSP/온프레미스를 가를 텍스트 근거 부족 — 근거 없는 확정 금지 원칙)

SELECT bucket_name, environment FROM failure_buckets ORDER BY created_at;
SQL

echo "[2/2] Document 미러 재동기화 (failure_buckets.environment != documents.environment 인 행만)"
docker compose exec -T api python3 <<'PY'
from sqlalchemy import select
from app.db.models import FailureBucket, Document
from app.db.session import session_scope
from app.failure_buckets.service import _index_bucket

with session_scope() as session:
    rows = session.scalars(select(FailureBucket)).all()
    stale = []
    for fb in rows:
        doc = session.get(Document, fb.document_id) if fb.document_id else None
        if doc is not None and doc.environment != fb.environment:
            stale.append(fb.id)

if not stale:
    print("동기화 필요한 행 없음 (이미 일치)")
else:
    for bucket_id in stale:
        result = _index_bucket(bucket_id)
        print(f"재색인: {bucket_id} -> {result}")
PY

echo "✅ 완료 — 아래로 최종 상태 확인:"
docker compose exec -T postgres psql -U citec -d citec_knowledge -c "
SELECT fb.bucket_name, fb.environment AS fb_environment, d.environment AS document_environment
FROM failure_buckets fb JOIN documents d ON d.id = fb.document_id
ORDER BY fb.created_at;
"
