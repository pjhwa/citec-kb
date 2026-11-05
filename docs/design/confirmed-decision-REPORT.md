# 사람 확정 결정 — 구현 보고 (2026-11-05)

브랜치 `main`(로컬 커밋만, **푸시·PR·머지 없음**). 확정 = 코드·시험·dev 측정으로 확인함, 추정/미확인은 그렇게 표시.

## 항목별

| ID | 수정 내용 | 테스트 | 측정 전후 | 남은 위험 |
|---|---|---|---|---|
| A2 | `mcp-server/ask_fold.py`: 문서(path)별로 그 문서를 가리키는 **모든** `[C#]`를 출처 한 줄에 병기(`- C7, C9, C10 … 제목`). 원인=문서 단위 중복 제거가 첫 청크 번호만 출력(개정 지시문 사실 12 확인) | `test_ask_fold.py` +1(불변식: 답변 본문 `[C#]` ⊂ 출처 번호) | dev 실호출에서 `C7, C9, C10, C11, …, C16 LINUX-재분류` 한 줄로 출력 확인 | 스킬이 `- C# …` 줄 형식을 파싱하는지 **미확인**(스킬 파일이 이 머신에 없음) |
| B1 | 모델 4개·`0021` 마이그레이션(`decision_verdicts` UPDATE/DELETE 차단 트리거)·`decisions/rules.py`·`service.py`·`routers/decisions.py`·작업 `decision_staleness_scan`(API+worker) | `test_decisions_unit.py`(7), `test_decisions_db.py` 18(append-only를 SQL로 직접 시도, nonce 단회, NFD 인용, 외부 근거 확인, 대체/보류/복구, 인증 on/off #11) | — | "근거 없는 확정 시도 거부 건수" 지표는 **미구현**(영속 카운터가 없음) |
| B2 | `decisions/mirror.py`: Document 미러(`confirmed_decision`, 등급 `H`), 의미 외 상태는 해시 밖 메타 | 미러 해시 안정(needs_recheck 전환 시 `content_hash`·`version` 불변), 상태↔Document.status(active/superseded/archived) | — | "유지" 판정은 판정일이 본문에 있어 해시가 바뀐다(Q7 허용, 1~2청크 재임베딩) |
| C1 | `GRADE_RANK["H"]=-1`, 필터 `confirmed_decision`(+별칭 `decision/decisions/confirmed`), `include_superseded`, 현행 확정 문서 점수 배수(`SEARCH_CONFIRMED_FACTOR`, 일반 규칙: source_type+상태), RAG 컨텍스트 최상단·확정자/확정일/재확인 태그·트러스트 이유 | `test_decisions_rag_unit.py`(4), DB 시험 중 순위·배수·recheck 미승격·superseded 제외, 하드코딩 부재(소스 grep 시험) | **E 게이트(확정 문서 0건 상태)** before/after 전 지표 동일, `compare` 종료코드 0 (`artifacts/citec-kb-decision-20261105/compare-C1.txt`) | 배수 1.5는 **시범 1건**으로 정한 잠정값. 결정이 늘면 재측정 필요 |
| C2 | `kb_ask` 출처 항목에 `사람 확정: 확정자 …, 확정일 …`·`⚠ 재확인 필요` | `test_ask_fold` 계열, `test_decisions_rag_unit` | dev 실호출에서 확정 문서 `C1` 선두 + 확정자 줄 확인 | **생성 답변 경로는 실측 못 함**: dev의 OpenRouter 크레딧 부족(HTTP 402)으로 스니펫 요약 폴백. 프롬프트 지시(확정자·확정일을 적어라)의 효과는 미확인 |
| D1–D3 | `decisions.html`(큐+지표), `decision.html`(카드), `css/decision.css`, 내비·홈·Admin 링크 | 헤드리스 Chrome 스크린샷으로 dev 실데이터 렌더 확인, 인라인 스크립트 `node --check` | — | **키보드 단축키·클릭 판정은 브라우저로 직접 눌러 보지 못함**(자동화 브라우저가 localhost:8572에 못 붙었음). API 흐름은 DB 시험으로 확인 |
| E1 | MCP `kb_list_decisions`·`kb_get_decision`·`kb_propose_decision`·`kb_add_decision_evidence`(판정 도구 없음), `kb_stats.decisions`(이름 붙인 건수+정의) | mcp 컨테이너에서 도구 4개 직접 호출(잘못된 인용 → 400 메시지 확인) | — | `mcp-server/test_*.py` 중 `mcp` 패키지가 필요한 시험은 이 venv에서 실행 불가(컨테이너에서만) |
| E2 | `AI_AGENT_GUIDE`(§4.12b, 등급 `H`, 안티패턴, D 절), `MCP.md`, `EXTERNAL_API.md`, 렌더 HTML | — | — | — |
| F | 시범 결정 "PIXEL 제공 종료일은?" (`tech_repo/80022224` 실문서 사용) | 아래 표 | 아래 표 | 시범 판정자는 **Claude Code(수용시험, 사람 아님)** — 확정자 필드에 그대로 기록됨. 사람이 다시 판정하려면 "유지" 대신 새 판정 |
| G | 안내 스텁(HTTP 410, MCP 문자열), 별칭·`insight` 허용값 제거(400+허용 목록), 서비스·라우터 본문·작업·`insights.html`·내비·`Insight` 모델 제거, `0022`(보관→삭제, `downgrade`=빈 스키마), 피드백을 `routers/feedback.py`로 분리 | `test_insights_retired_unit.py`, `test_drop_insights_migration_db.py`(upgrade↔downgrade 왕복) | dev: insight 문서 3→0, 스모크 통과 | 스텁은 다음 릴리스에서 제거해야 함. Neo4j의 insight 노드 정리는 **미확인** |

## F 수용 기준

| # | 결과 |
|---|---|
| 1 | 기준선 `kb_search("PIXEL 제공 기한 PISA Station")` dev 상위 10에 원문 없음 (`baseline-pixel-search.txt`). trust는 dev에서 `strong`(지시문 관측은 `weak` — 환경 차이) |
| 2 | 판정 후 **1위**(점수 0.0234, 배수 1.5). 배수별: 1.0→32위 / 1.25→2위 / 1.5→1위 / 2.0→1위. 하드코딩 없음(소스 grep 시험). 무관 질의 3건에서는 확정 문서가 상위에 끼지 않음 |
| 3 | 확정 문서가 `C1`로 인용·확정자/확정일 표시 **확인**. `[C#]` 불변식은 시험으로 확인. 생성 답변 실측은 **불가**(402) |
| 4 | E 게이트: 확정 문서 0건에서 전 지표 동일(홀드아웃 recall@10 0.533/0.500, evalset 군별 동일), 종료코드 0. **확정 문서가 있는 상태의 게이트는 재지 않았다**(18분+ 소요, 시범 1건이 evalset 질의에 걸릴 가능성은 낮으나 미측정) |
| 5 | 존재하지 않는 인용문·`document_id` → 400, NFD 입력 통과(단위·DB 시험) |
| 6 | 판정 UPDATE/DELETE는 DB 트리거가 거부(시험) |
| 7 | 근거 문서 해시 변경 → 스캔 후 `needs_recheck`·미승격(점수 = 기준) → "유지" 1회로 `confirmed`·재승격(시험) |
| 8 | 대체 후 기본 검색 제외, `include_superseded=true`로 노출(시험) |
| 9 | 판정 1회 = 선택지 클릭/키 1회(코드). 근거는 같은 화면. 목업·UI 렌더로 확인, 키보드 조작은 직접 미확인 |
| 10 | G 스모크·마이그레이션 왕복 통과. 스킬 패치 제안은 아래 |
| 11 | 인증 전환(apikey): 익명 401, viewer 403, senior 통과, `decided_by` 위조 무시, off 복귀 시 자기 신고(시험) |

## 추가 작업 (2026-11-05 후속): 주제 자동 도출 + 실패 버킷 연계 + 매칭 개선

| 항목 | 내용 | 확인 |
|---|---|---|
| 주제 스캔 | `decisions/scan.py`·`POST /v1/decisions/_scan-topics`·작업 `decision_topic_scan`(worker). 규칙 기반(LLM 없음) 탐지기 3종 `fb_confirm`/`deadline`/`bucket_gap`, `source_key`로 멱등, 상한 2종, dry-run | DB 시험 5건. dev 실행: 탐지 fb 7(SMOKE·시험 버킷 제외)·기한 1(PIXEL, 위험 높음)·공백 7 → 15건 등록, 재실행 0건 신규, 작업 큐 경유 실행 확인 |
| 실패 버킷 연계 | `confirmation`(결정 id·상태·판정·확정자)을 버킷 상세·목록·match 응답에 붙임. **판정이 버킷을 바꾸지 않음**(확정 층과 기계 신뢰도 분리). 질문은 확정/보완/폐기 3택 | DB 시험(verdict 후 버킷 confidence·status 불변) |
| 매칭 (가) | `failure_buckets/terms.py` 한/영 용어 그룹(쿼럼=quorum, 펜싱=fencing, 스플릿 브레인=split-brain 등 + 승인된 검색 동의어). 조사(을/를/에서…)와 하이픈·공백 표기 흡수. 의미 유사 일치 없음 | 단위 시험 7건(없는 용어는 일치 안 함 포함) |
| 매칭 (나) | `match_evidence`(신호별 겹친 단어, `via_term_group`·`observed_as`↔`signal_as`), `contradicted_evidence`, MCP 출력에 "겹친 단어" 줄 | 시험 |
| 매칭 (다) | `label_basis`(일치 n/전체, 분모, confidence, 조건부·가능에 필요한 일치 수, 미확인 신호)와 `label_rules`. **임계값은 바꾸지 않았다**: confidence 0.5 미검증 버킷은 '가능'에 계산 대상 신호 전부 일치가 필요, 4개 중 1개는 비권고(0.35)가 의도된 보수성 | 시험 |
| 요청서 해석 정정 | 요청서 관찰 3("증상 글은 영향 없음")은 코드와 다르다 — 증상 토큰은 처음부터 일치에 쓰였고, 한국어 표기 때문에 일치하지 않았을 뿐이다 | 코드 확인 |
| 한계(실측) | dev의 실제 버킷 B(`6ea28d59`)는 저장 신호가 요청서 표와 다르다(예: "Signaling sbd to panic … 새 커널 부팅"). 그래서 "양 노드 자기 펜싱 후 재부팅"은 **여전히 일치하지 않는다** — 목록에 없는 말(panic·sbd)은 같은 말로 보지 않는다. `쿼럼`만 단독으로는 6개 단어 신호 중 1개라 일치하지 않는다(절반 규칙 유지) | dev match 실호출 |

용어를 더 늘리려면 `FB_TERM_GROUPS`에 사람이 합의한 쌍만 추가하거나 기존 동의어 마이닝(`app.synonyms`) 승인 경로를 쓴다(자동 반영됨).

## 승인이 필요한 운영 작업 (별도 목록)

1. 운영 DB `0021`, `0022` 적용 — `docs/DECISION_INSIGHT_RETIREMENT_RUNBOOK_20261105.md` (백업·순서·롤백 포함).
2. `decision_staleness_scan` 주기 실행 등록.
3. 인증 켜기(시범 이후): `AUTH_MODE`+토큰.
4. 새 `confirmed_decision` 문서의 운영 임베딩(판정 시 자동, 모델 가용 필요).
5. 다음 릴리스에서 폐기 스텁 제거.
6. 운영 `0023` 적용과 `scripts/decision_scan_cron.sh` crontab 등록(dev crontab에도 설치하지 않았다).

## 스킬 `citec-mcp-workbench` 패치 제안 (스킬 파일은 수정하지 않았다)

| 변경 | 바꿀 곳 |
|---|---|
| 새 등급 `H`·`source_type=confirmed_decision`·필터 별칭 `decision/decisions/confirmed` | `references/wiki-mcp.md` 소스·신뢰도 표 |
| 확정 문서 인용 시 확정자·확정일·재확인 여부 병기. 출처 줄이 `- C1, C11 제목` 형태로 바뀜(A2) — `- C# …` 정규식이면 `[C#(, C#)*]` 허용 | `SKILL.md` 인용 규칙 |
| `decision.recheck=true`/`effective_status=needs_recheck`는 사실로 단정하지 않고 원문 확인 | `references/kb-limits.md` |
| `kb_propose_decision`/`kb_add_decision_evidence` 사용법(판정 도구 없음, 인용은 원문 글자 그대로, 외부는 URL+스냅샷+시각) | `references/wiki-mcp.md` 도구표 |
| `kb_list_insights`/`kb_get_insight`/`wiki_*_synthesis` 폐기(스텁→제거), 필터 `insight/insights/synthesis` 400, `kb_stats`의 `insights=` 출력 제거 → `decisions` 블록 | `references/wiki-mcp.md`, `references/kb-limits.md`, `SKILL.md` 도구 라우팅 요약 |

별칭 `insights`/`synthesis`를 스킬이 쓰는지는 **확인하지 못했다**(스킬 파일이 이 머신에 없음).

## 열린 것

- dev에 남은 시험 결정 1건(`6190ab7f…`, 확정자="수용시험(Claude Code, dev) — 사람 판정 아님")을 지울지 보류(retire)할지. 하드 삭제 경로는 없고, 보류하면 검색에서 빠진다.
- Q6~Q9는 설계 문서 권고안대로 구현했다(NEI는 기준이 되지 않음, 유지 시 재임베딩 허용, 철회는 `retracted_at`, `decision:` 접두사 추가).
