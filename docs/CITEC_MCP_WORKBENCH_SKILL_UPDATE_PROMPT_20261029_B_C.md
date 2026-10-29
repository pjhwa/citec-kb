# citec-mcp-workbench 스킬 갱신 프롬프트 — 서버 B1~B4, C 반영분 (2026-10-29)

`CITEC_MCP_WORKBENCH_SKILL_UPDATE_PROMPT_20261028.md`(A1~A7)의 **후속**이다. 스킬 저장소의 Claude Code에 붙여 쓰고, 스킬 파일만 수정한다.

## 0. 적용 시점 (운영 확인됨)
- B1~B4는 운영(`70.222.78.8:8572`)에 **배포되어 측정까지 끝났다**(2026-10-29, 배포 전/후 동일 평가셋). A1~A7도 같은 배포에 포함됐다. 따라서 A·B 우회는 아래 표대로 줄여도 된다.
- C(`generation_status`)는 코드가 dev 저장소에 있고, **운영 배포 여부는 `kb_ask` 첫 줄에 `⚠ 생성 실패`가 보이는지로 확인**한다(운영은 아직 Fabrix chat 어댑터가 없어 항상 폴백이다).

## 1. 운영 측정 결과 (우회 판단 근거)
| 항목 | 배포 전 | 배포 후 |
|---|---|---|
| Linux OOM 점검항목 (`section=checkitem`, `Linux OOM`/`OOM`/`Linux Out of Memory`) | 2/6 | 6/6 (순위 1·2·1) |
| 필터 없는 검색 상위 5 중 pointer-only(confluence_map) 비율 (홀드아웃) | 49.3% (multi_query 끔 60.7%) | 8.0% (끔 4.7%) |
| 일반 키워드 probe 8건의 pointer-only 비율 | 61.3% | 10.6% |
| 증상 문장 검색, 필터 없음 | 3/7 | 6/7 |
| 유사장애 | 6/7 | 7/7 |
| "2026년 9월 기술지원 목록" | hybrid_search | `time_scoped_list` (component=기술지원 → 25건) |
| 홀드아웃 recall@10 (multi_query 켬/끔) | 40.0% / 30.0% | 46.7% / 43.3% |

## 2. 서버 동작 변경과 스킬 패치
| 항목 | 서버 | 스킬 패치 |
|---|---|---|
| B1 | `section=checkitem` 또는 `area` 지정 시 `PISAOLNX` 접두 확장 안 함. `OOM`↔`Out of Memory`↔`메모리 부족`↔`oom-killer`, `kernel panic`↔`커널 패닉`, `failover`↔`페일오버`, `deadlock`↔`데드락`, `memory leak`↔`메모리 누수`, `k8s`↔`Kubernetes`↔`쿠버네티스` 검색 동의어 | `wiki-mcp.md` PISA 점검항목 행: "직접 매칭 없으면 증상·파라미터명으로 변경"은 **재질의 1회 정도의 보조 수단**으로 완화. 약어 → 영문 풀어쓰기 수동 확장 지시는 위 목록에 한해 삭제 |
| B2 | 필터 없는 검색에서 pointer-only 행 점수 ×0.85(`SEARCH_POINTER_FACTOR`). `section=confluence_map`이면 적용 안 함 | "필터 없는 검색은 map이 채운다 → 항상 `section` 지정" 지시를 **"가능하면 section 지정, 안 해도 상위가 map으로만 차지되지는 않는다"**로 완화. map 근거는 계속 "pointer, 원문 미확인"으로 표기 |
| B3 | 4단어 이상 문장형 질의에서 코퍼스 희소 토큰 AND 변형을 후보에 추가 | "증상 문장은 `kb_similar_incident` 먼저" 규칙은 **유지**(여전히 가장 정확). 단 `kb_search` 증상 검색이 상위 10에 들어올 가능성이 올랐으므로 "검색은 안 된다"는 단정은 삭제 |
| B4 | `YYYY년 M월`, `YYYY년 M월부터 N월까지`가 기간 목록으로 분기. 질의 속 연도는 support_history 소프트 부스트 | `kb_query` 행의 "연월 표현은 기간 질의로 분기 안 됨" 삭제. 단 "기술지원" 같은 component 단어가 있으면 그 component로 좁혀 **건수가 전체보다 작을 수 있다**고 안내(예: 2026-09 전체 38건 vs 기술지원 25건). 전체 건수가 필요하면 `kb_list_tickets(date_from, date_to)` |
| C | `/v1/chat`·`kb_ask` 결과에 `generation_status`(`ok`/`failed_snippet_fallback`/`empty_snippet_fallback`/`not_attempted`). 실패 시 trust는 weak 이하, 폴백 `[C#]`는 인용으로 세지 않음. MCP 첫 줄: `[스니펫 요약] ⚠ 생성 실패 — …`. 출처는 문서당 1건 | `kb_ask` 행: 첫 줄이 `[스니펫 요약]`/`⚠ 생성 실패`이면 **LLM 답변이 아니라 검색 스니펫**이므로 사실 근거로 쓰지 말고 출처를 `kb_get_document`로 열어 확인. 운영은 Fabrix chat 미구현이라 **현재 항상 이 경우**다. `kb_ask`를 라우팅의 기본 답변 수단으로 쓰지 않는다 |

## 3. 아직 우회를 유지할 것
- **파일시스템 inode 질의**: `Linux 파일시스템 inode 사용률`이 정답 `PISAOLNX_04.04.03`을 8위에 둔다(운영 동일). 원인은 `multi_query`가 일반 헤드(`파일 시스템`)의 점수를 그대로 max-merge하는 방식이라 B1~B4로는 풀리지 않았다. 이런 질의는 `kb_list_checkitems(q="inode")` 또는 제목 키워드 단독 검색을 우선한다.
- **CITECTS-2502 증상 문장(필터 없음)**: 10위. `section="support_history"` 또는 `kb_similar_incident`를 유지.
- **번호가 있으면 `kb_ticket` 먼저**: 유지.
- **`kb_ask` 생성 답변**: Fabrix chat 스펙 확정 전에는 복원하지 않는다.
- D(frame 충족률 6.1%, lexicon·그래프 백필)는 아직 반영되지 않았다.

## 4. 스모크 (운영)
| 질문 | 기대 |
|---|---|
| "Linux OOM 점검항목" | `section="checkitem"`로 1~2회 호출 내에 `PISAOLNX_02.01.02` |
| "Redis timeout" (필터 없음) | 상위 5가 모두 map 포인터가 아님 |
| "2026년 9월 기술지원 목록" | 기간 목록, 건수 = `kb_list_tickets(2026-09-01~09-30, component=기술지원)` |
| "kb_ask 아무 질문" | 첫 줄 `[스니펫 요약] ⚠ 생성 실패 …`, 사실 인용 금지 |

## 5. 하지 말 것
- inode, 2502(필터 없음)의 우회를 지우지 않는다.
- `kb_ask` 첫 줄 문구(`[스니펫 요약]` 접두)를 조건으로 쓰는 분기가 있으면 접두는 유지되므로 그대로 둔다.
- 서버 저장소를 수정하지 않는다.
