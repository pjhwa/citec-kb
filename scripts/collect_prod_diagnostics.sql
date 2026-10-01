-- collect_prod_diagnostics.sql — citec-kb 운영 DB 진단 수집 (read-only).
--
-- 이 세션(2026-09-30/10-01, PR #1)에서 BLOCKED_EXTERNAL로 남긴 항목들을
-- 확인하기 위한 조회 전용 쿼리 모음. 쓰기 쿼리는 전혀 없다 — INSERT/UPDATE/
-- DELETE/DDL 없음, SELECT와 EXPLAIN(실행 계획, 읽기 전용)만.
--
-- 실행: scripts/collect_prod_diagnostics.sh 로 감싸서 실행 (아래 참고).
-- 직접 실행하려면:
--   psql "$DATABASE_URL" -f scripts/collect_prod_diagnostics.sql > diagnostics_$(date +%Y%m%d).txt
--
-- 각 섹션은 REPORT.md(artifacts/citec-kb-improvement-implementation-20260930/)
-- §2 BLOCKED_EXTERNAL 또는 REVIEW.md의 특정 항목에 대응한다 — 섹션 제목에 표기.

\echo '=== [0] 배포 상태 (alembic 버전, 테이블 수) ==='
SELECT
  (SELECT version_num FROM alembic_version LIMIT 1) AS alembic_revision,
  (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public') AS public_tables,
  now() AS collected_at;

\echo ''
\echo '=== [1] P0-B / §5: OVE 자식·부모 페이지의 ancestor_ids 실제값 ==='
\echo '-- "부모(2525893483) 제외해도 자식(2553464543)이 v1에서 남았다" 재현의'
\echo '-- 정확한 원인(조상 데이터 결측 vs 배포 상태)을 가리기 위한 조회.'
SELECT
  id, source_type, external_id, status,
  jsonb_typeof(metadata->'ancestor_ids') AS ancestor_ids_type,
  metadata->'ancestor_ids' AS ancestor_ids,
  updated_at
FROM documents
WHERE external_id IN ('2553464543', '2525893483')
ORDER BY source_type, external_id;

\echo ''
\echo '-- 같은 두 page_id가 confluence_map 외 다른 source_type에도 있는지'
\echo '-- (REVIEW.md "맵 이외 Confluence 사본으로 우회할 여지") — 있다면 그 행의'
\echo '-- ancestor_ids도 위와 함께 확인해야 한다.'
SELECT source_type, COUNT(*) FROM documents
WHERE external_id IN ('2553464543', '2525893483')
GROUP BY source_type;

\echo ''
\echo '=== [2] P0-B / §5: source별 ancestor_ids 결측 분포 (coverage_gaps와 동일 쿼리) ==='
SELECT
  source_type,
  COUNT(*) AS total_active,
  COUNT(*) FILTER (WHERE NOT (metadata ? 'ancestor_ids')) AS missing_ancestor_ids,
  COUNT(*) FILTER (WHERE NOT (metadata ? 'source_version')) AS missing_source_version
FROM documents
WHERE status = 'active' AND source_type IN ('confluence_map', 'confluence_docs', 'tech_repo')
GROUP BY source_type
ORDER BY source_type;

\echo ''
\echo '-- confluence_map만: tech_relevant 분류 결측/분포'
SELECT
  COUNT(*) AS total_active,
  COUNT(*) FILTER (WHERE NOT (metadata ? 'tech_relevant')) AS missing_tech_relevant,
  metadata->>'tech_relevant' AS tech_relevant_value,
  COUNT(*) AS n
FROM documents
WHERE status = 'active' AND source_type = 'confluence_map'
GROUP BY GROUPING SETS ((), (metadata->>'tech_relevant'));

\echo ''
\echo '=== [3] P1-A / D07: classify_map_tech 재평가용 원본 데이터 (최대 5000건) ==='
\echo '-- title+excerpt+기존 tech_relevant/citec_domains를 뽑아 개발서버에서'
\echo '-- classify_map_tech() 신/구 로직으로 재실행해 실제 블라스트 반경을 잰다.'
\echo '-- (로컬 data/raw/confluence_map/에는 이 필드들이 전혀 없어 세션 중'
\echo '-- 측정 불가했던 부분 — REPORT.md §1-C "검증 한계" 참고)'
\copy (SELECT external_id, title, LEFT(body_md, 500) AS body_excerpt, metadata->>'tech_relevant' AS tech_relevant, metadata->'citec_domains' AS citec_domains FROM documents WHERE status='active' AND source_type='confluence_map' AND metadata ? 'tech_relevant' ORDER BY random() LIMIT 5000) TO STDOUT WITH CSV HEADER

\echo ''
\echo '=== [4] P1-B / §9: PIXEL 80022224 사례 (v97 공지 누락 재현 확인) ==='
SELECT id, source_type, external_id, title, status,
  metadata->>'최종수정일' AS last_modified_field,
  metadata->'source_version' AS source_version,
  metadata->'ancestor_ids' AS ancestor_ids,
  updated_at, source_uri
FROM documents
WHERE external_id = '80022224';

\echo ''
\echo '=== [5] health documents_count vs active_documents_count 실제 차이 ==='
SELECT status, COUNT(*) FROM documents GROUP BY status ORDER BY status;
\echo ''
SELECT source_type, status, COUNT(*) FROM documents GROUP BY source_type, status ORDER BY source_type, status;

\echo ''
\echo '=== [6] P1-B §9: source별 ingest 체인 커버리지 (coverage_gaps 전체) ==='
SELECT
  d.source_type,
  COUNT(DISTINCT d.id) FILTER (WHERE d.status='active') AS documents_active,
  COUNT(DISTINCT c.id) AS chunks,
  COUNT(DISTINCT c.id) FILTER (WHERE c.is_active) AS chunks_active,
  COUNT(DISTINCT e.id) AS embeddings
FROM documents d
LEFT JOIN chunks c ON c.document_id = d.id
LEFT JOIN embeddings e ON e.chunk_id = c.id AND c.is_active
GROUP BY d.source_type
ORDER BY d.source_type;

\echo ''
\echo '-- support_history/incident_reports: issue_frames 결측'
SELECT
  d.source_type,
  COUNT(*) FILTER (WHERE d.status='active') AS total_active,
  COUNT(*) FILTER (WHERE d.status='active' AND f.id IS NULL) AS missing_frame
FROM documents d
LEFT JOIN issue_frames f ON f.document_id = d.id
WHERE d.source_type IN ('support_history', 'incident_reports')
GROUP BY d.source_type;

\echo ''
\echo '-- issue_frames의 body_hash/extractor_version 결측 (이번 PR의 P0-C 마이그레이션'
\echo '-- 적용 직후라면 전부 NULL이 정상 — 다음 extract_frames() 실행 전까지는'
\echo '-- "최초 1회 재생성 대상"이라는 뜻. §13 "코드 배포됨"과 "기존 데이터 완료"는'
\echo '-- 별도 상태로 본다는 원칙 그대로.'
SELECT
  COUNT(*) AS total_frames,
  COUNT(*) FILTER (WHERE body_hash IS NULL) AS missing_body_hash,
  COUNT(*) FILTER (WHERE extractor_version IS NULL) AS missing_extractor_version
FROM issue_frames;

\echo ''
\echo '=== [7] P0-B §5: exclude_subtree_ids 쿼리의 실행 계획 (EXPLAIN만, ANALYZE 아님 — 안전) ==='
\echo '-- 일반 metadata GIN 인덱스가 이 JSONB ?| 연산에 실제로 쓰이는지 확인.'
EXPLAIN
SELECT id FROM documents
WHERE status = 'active'
  AND external_id NOT IN ('2553464543', '2525893483')
  AND NOT COALESCE(metadata->'ancestor_ids' ?| ARRAY['2553464543','2525893483'], false);

\echo ''
\echo '=== [8] 배포 SHA (운영 코드가 어느 커밋인지) ==='
\echo '-- 이 쿼리는 DB가 모른다 — scripts/collect_prod_diagnostics.sh가 git/파일로 별도 확인'

\echo ''
\echo '=== 수집 완료 ==='
