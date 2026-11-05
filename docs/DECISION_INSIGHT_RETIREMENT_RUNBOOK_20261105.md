# 운영 적용 절차 — 사람 확정 결정(0021) 도입과 insight 폐기(0022)

작성 2026-11-05. **이 문서는 절차 초안이다. 운영에는 아직 아무것도 적용하지 않았다.** 적용은 파트장 승인 뒤 운영 담당이 한다.

## 1. 무엇이 바뀌나

| 항목 | 0021 `decisions` | 0022 `drop_insights` |
|---|---|---|
| DB | 새 테이블 4개(`decisions`, `decision_evidence`, `decision_verdicts`, `decision_nonces`) + `decision_verdicts` 불변 트리거 | `insights` 테이블 삭제, `source_type='insight'` 문서 → `archived` → 임베딩·청크·섹션·문서 삭제, `feedback.target_type='insight'` 행 삭제 |
| 되돌림 | `alembic downgrade` — 테이블·트리거 제거(판정 이력이 사라진다) | `downgrade`는 **빈 `insights` 스키마만** 복구한다. 삭제된 insight 행·문서는 복구 불가 → **백업 필수** |
| 영향 | 기존 기능 무영향(신규 테이블). 검색 코드는 확정 문서가 없으면 이전과 동일(dev 평가 게이트 `compare-C1.txt`: 전 지표 동일) | 색인된 insight 문서 사라짐(dev 기준 3건), `/v1/insights*`·`/api/synthesis*`는 410, `section=insight/insights/synthesis`는 400, MCP `kb_list_insights` 등은 폐기 안내 문자열 |

## 2. 순서 (운영)

0. **사전 확인(읽기 전용):** 운영의 `insights` 행 수와 상태별 건수, insight 문서 수를 기록한다.
   `select status,count(*) from insights group by 1;` / `select count(*) from documents where source_type='insight';`
   → 시험용이 아닌 항목이 있으면 중단하고 보고한다(dev 23건은 모두 시험·스모크 제목이었다).
1. **백업:** `scripts/backup_postgres.sh` (복구 시험 포함 `docs/BACKUP_RESTORE.md`).
2. **코드 배포:** 새 이미지(api, worker, mcp, web). 이 시점에 `/v1/insights*` 는 이미 410 스텁이다 — 먼저 코드를 올리면 마이그레이션 전에도 insight 쓰기가 막힌다.
3. **0021 적용:** `alembic upgrade 20261105_0021` → `GET /v1/decisions/metrics`가 200.
4. **0022 적용:** `alembic upgrade head`. 소요는 insight 문서 수에 비례(수 건이면 초 단위).
5. **스모크:** `kb_stats`(`decisions` 블록), `kb_search("…", section="confirmed_decision")`, `kb_list_insights`(폐기 안내 확인), `/decisions.html` 열기.
6. **지식그래프:** Neo4j에 insight 문서 노드가 있으면 다음 `graph_sync` 배치가 정리하는지 확인한다(미확인 — dev에서 그래프 정리는 시험하지 않았다). 남아 있으면 `kb_graph_status`로 보고하고 수동 정리는 별도 승인.
7. **작업 큐:** 대기 중인 `insight_reindex` 작업이 있으면 처리 전에 비운다(0022 이후 worker가 알 수 없는 작업으로 실패한다). 새 작업 `decision_staleness_scan`은 아래 3항대로 예약한다.

## 3. 운영 중 반복해야 하는 것 (승인 필요)

- **낡음 스캔 예약:** `POST /v1/jobs {"type":"decision_staleness_scan"}`(admin)을 하루 1회 등. 스캔이 돌기 전에는 상태가 저장되지 않는다(API 조회 응답의 `effective_status`는 항상 지연 계산으로 맞다). 검색 순위 중단은 스캔이 `needs_recheck`를 저장한 뒤부터다 — 근거 문서 변경 직후 스캔 전까지는 승격이 유지될 수 있다.
- **인증 켜기:** 시범 이후. `AUTH_MODE`와 토큰만 바꾸면 판정 라우트가 `senior|admin`을 요구하고 `decided_by`는 `principal.name`이 된다(시험 `test_auth_on_gates_and_ignores_forged_decided_by`). 켜기 전까지 확정자는 자기 신고다.
- **`SEARCH_CONFIRMED_FACTOR`**(기본 1.5): 결정이 늘면 같은 방법(`scripts/verify/eval_retrieval_gate.py` + 대표 질의 순위표)으로 다시 잰다. 1.0 = 끔(롤백 스위치).

- **주제 스캔 예약:** `scripts/decision_scan_cron.sh`(스캔+낡음 스캔 작업을 큐에 넣음). 호스트 crontab에 직접 추가한다(자동 설치 안 함):
  `30 6 * * * /home/citec/dev/citec-kb/scripts/decision_scan_cron.sh >> /home/citec/dev/citec-kb/logs/cron_decision_scan.log 2>&1`.
  인증을 켠 뒤에는 `API_TOKEN`(admin)이 필요하다. 상한·탐지기는 `config/decision_scan.json`(예시 파일 참고). 먼저 `POST /v1/decisions/_scan-topics?dry_run=true`로 무엇이 올라올지 본다.
- 마이그레이션 `0023`(`decisions.source_key/source_ref`, 부분 유일 인덱스)은 0021·0022와 같은 절차로 적용한다. `downgrade`는 두 컬럼과 인덱스만 제거한다.

## 4. 롤백

- 즉시 완화: `SEARCH_CONFIRMED_FACTOR=1.0`(확정 문서 승격 끔). 판정 UI는 `nav.js`/라우터를 되돌리지 않고도 사용하지 않으면 영향 없음.
- 0022만 되돌리기: `alembic downgrade 20261105_0021` → 빈 `insights` 테이블. 데이터는 백업에서 복원.
- 둘 다: `alembic downgrade 20261105_0020`(결정 이력 삭제됨을 인지).

## 5. dev 검증 결과 (2026-11-05)

- `scripts/scratch_db_test.sh`: `tests/test_drop_insights_migration_db.py`(0022 upgrade/downgrade 왕복, 다른 문서·답변 피드백 보존), `tests/test_decisions_db.py` 18건 통과.
- dev DB(`citec_knowledge`)에는 0021·0022를 모두 적용했다(적용 전 `insights` 데이터는 `pg_dump --data-only`로 따로 저장). 적용 뒤 스모크: insight 문서 3→0건, `/v1/insights`·`/api/synthesis` 410, `section=insight` 400(허용 목록 포함), `kb_stats`에 `decisions` 블록만. 운영은 이 절차를 승인받아 별도로 적용한다.
- dev insight 23행(approved 6, draft 10, review 6, rejected 1) 전부 시험·스모크 픽스처(`mock-idp-e2e`, `auth-off smoke`, `Reject me`, `ZXREINDEX…` 등). 승격 문서 3건(`Async promote e2e`, `Insight index smoke…`, `E2E Insight: Linux hang 진단 체크리스트`)도 출처 문서 0건의 e2e 픽스처라 **결정 제안으로 옮기지 않는다**(근거 인용이 없어 새 모델의 근거 게이트를 통과할 수 없고, 내용이 시험 문구다).
- `prod_pull_export.sh`/`prod_pull_apply.sh`는 운영 DB를 통째로 dev에 덮는다(`docs/DEPLOY.md`). 운영에 0022가 적용되기 전에 그 스크립트로 dev를 덮으면 dev에도 `insights` 테이블이 되살아난다 → `alembic upgrade head`를 다시 실행한다.
