# citec-mcp-workbench 스킬 갱신 프롬프트 — 서버 A1~A7 반영분 (2026-10-28)

대상: 사용자 PC의 `citec-mcp-workbench` 스킬(사내 마켓플레이스 PR #160 계열). 이 문서는 citec-kb 서버 쪽
`CITEC_KB_RETRIEVAL_AND_TOOL_FIX_PROMPT_20261028.md` 의 **A 단계(A1~A7)** 를 dev 저장소에 반영한 뒤, 스킬에서 무엇을
고쳐야 하는지 적은 것이다. B(검색 품질)·C(`kb_ask`)·D(데이터 백필)는 아직 서버에 반영되지 않았으므로 **그 항목의 우회 규칙은
지금 지우지 않는다.**

> 이 프롬프트를 스킬 저장소의 Claude Code에 그대로 붙여 쓴다. 스킬 파일만 수정하고, 서버 저장소는 건드리지 않는다.

---

## 0. 먼저 알아둘 것 — 적용 시점

- 아래 변경은 **dev 저장소 커밋**(A1~A7, 한 항목 한 커밋, 마지막 `f174bd8`)이다. 운영(`70.222.78.8:8572`)에 `git_pull_deploy.sh`로 배포되기
  전에는 운영 서버가 예전 동작이다. **운영 배포 확인 후에 스킬의 우회를 지운다.** 확인 방법(스킬이 쓸 수 있는 것):
  - `kb_title_tokens()` 가 토큰을 출력하면 A1 반영.
  - `kb_search("x", section="checkitems", limit=1)` 이 400/오류 문구가 아니라 결과(또는 정상 "결과 없음")를 주면 A4 반영.
  - `kb_ticket("citects-2502")` 가 본문을 주면 A5 반영.
- 확실하지 않으면 **우회를 남기되 "서버가 고쳐졌으면 생략 가능"을 병기**하는 쪽으로 쓴다(`kb-limits.md` 머리말의
  "서버가 고쳐졌을 수 있다" 문장과 같은 방식).

## 1. 서버 동작이 바뀐 것 (스킬이 의존하던 가정과 비교)

| 항목 | 이전 서버 | 바뀐 서버 | 스킬 영향 |
|---|---|---|---|
| A1 `kb_title_tokens` | 항상 토큰 0개 | 상위 토큰·건수·점유율(%)과 `document_n`, `unique_tokens` 출력. 토큰이 없으면 "토큰이 없습니다" 문장 | REST 우회 불필요 |
| A2 `kb_entity_share` | 분자(전체 기간 링크 수) > 분모 → share 1.47 | 분자·분모 모두 같은 기간·소스에서 `kb_analytics(entity=)` 와 같은 정의로 계산. `matched <= total` 보장. 출력은 `matched(엔티티 포함 건수)=… / total(분모: 같은 기간·소스 전체)=… → share=…` + `정의:` 한 줄. `kb_query` 의 엔티티 비중 질문도 같은 라벨로 출력 | 사용 금지 해제 가능(단 정의 주의 — 아래) |
| A3 잘못된 인자 | `group_by=bogus`→year 집계, 틀린 도메인→"패턴 없음", `limit=0`→1건 | `kb_analytics(group_by=…)`, `kb_citec_recurring_patterns(domains=, severity_tiers=)`, `kb_match_failure_bucket(fb_domain=, environment=)` 가 허용 값 목록과 함께 `status: invalid_request` 로 거부(대소문자는 허용 값에 맞춰 정규화). `kb_search(limit<1)` 거부, `limit>50` 은 50으로 보정하고 결과 머리에 `[note: limit N→50으로 보정]` | "헤더로 group 기준 확인" 주의 완화, 오류 문구를 그대로 읽고 허용 값으로 재호출 |
| A4 section/area | `checkitems`(복수) 조용히 0건, `area=Linux` 0건 | `section` 별칭(`checkitems`, `incident`, `insights`, `swim` 등)과 대소문자를 서버가 정규화. 모르는 section 은 400+허용 목록. `area` 는 대소문자 무시, 어떤 문서에도 없는 값이면 결과 머리에 `[경고] area/domain '…'에 해당하는 문서가 없습니다` | 함정 문구 삭제(단수 `checkitem`·소문자 지시는 계속 동작하므로 남겨도 무방) |
| A5 `kb_ticket` | `citects-2502`, `2502` → 찾을 수 없습니다 | 대소문자·`CITECTS 2502`·`citects2502` 는 자동 해석하고 첫 줄에 `(입력 'citects-2502' → CITECTS-2502 로 해석)`. 숫자만(`2502`)은 **자동 확정하지 않고**, 존재하면 `혹시 CITECTS-2502 인가요?` 를 덧붙임. 없는 번호의 문구 `오류: 티켓을 찾을 수 없습니다: …` 는 **그대로** | 가짜 번호 판별 규칙은 유지(오류 문구 동일) |
| A6 식별자·trust | MCP 출력에 trust 없음, 없는 번호에도 무관한 10건 | `kb_search`/`kb_query`(검색 분기) 머리에 `trust=<strong\|medium\|weak\|empty> exact_tokens=[…]`. 질의의 문서 ID 형태 토큰(`CITECTS-####`, `ISS-…`, `PISA…_nn.nn`, `confluence_<id>`, 9자리 이상 숫자)이 활성 문서 어디에도 없으면 `[경고] 질의의 식별자 X 는 코퍼스에서 찾지 못했습니다 …` 줄이 붙고, 식별자가 **전부** 없으면 trust 는 weak 로 내려감(일부만 없으면 trust 유지+경고) | "번호가 있으면 `kb_ticket` 먼저" 규칙은 **유지**(더 싸고 확실). 검색 결과를 쓸 때 `[경고]`·`trust=weak` 를 반드시 확인하는 규칙 추가 |
| A7 그래프 라벨 | `연관 과거 장애` (실제로는 FailureBucket 목록) | `연관 failure bucket`. 0건이면 "등록된 failure bucket이 연결되지 않았다는 뜻일 뿐 과거 장애가 없다는 뜻이 아닙니다…" 안내 줄. 과거 장애 티켓은 `연관 문서` 에 섞여 나옴. `snapshot` 태그는 "크롤 시점 저장본"의 뜻으로 문서 통일(본문 전체 포함 여부와 무관) | 라벨/설명 갱신 |

바뀌지 않은 계약(스킬이 그대로 의존해도 됨): 도구 이름·인자, `kb_search` 결과 줄 형식(`[source_type] 제목 score=… (external_id)` /
`path:` / `web_url:` / `snippet:`), `kb_graph_explore` 첫 줄 `status:` 와 `[Nhop 경로]`, `kb_similar_incident` 적용성 문구,
`section="checkitem"`(단수)·소문자 `area` 지시, `web_url` 이 `http://localhost:8572/...` 로 나오는 것(이번 범위 밖 — 치환 규칙 유지).

## 2. 파일별 패치 지시

### `references/kb-limits.md`
머리말의 ✔ 목록에서 해소된 항목(`kb_title_tokens` 빈 결과, `section=checkitems` 0건, `entity_share` 1.47)을 "2026-10-28 이후 서버에서
수정됨(dev 반영, 운영 배포 확인 필요)"로 바꾸거나 삭제한다.

§1 표:
- `허위·오타 티켓 번호(CITECTS-99999)…` 행 → 증상을 "서버가 `[경고]` 와 `trust=weak` 를 표시하게 됨(A6). 그래도 질문에 번호가 있으면 `kb_ticket` 먼저"로
  완화하고, 우회 칸에 "검색 결과 머리의 `[경고] 질의의 식별자…` 가 보이면 그 번호는 코퍼스에 없는 것으로 보고 결과를 근거로 쓰지 않는다"를 추가.
- `kb_ticket("citects-2502")·("2502")는 부재 처리` 행 → 삭제. 대신 한 줄: "`kb_ticket` 은 대소문자·공백 변형을 해석하고, 숫자만 주면 `혹시 CITECTS-#### 인가요?` 제안만 한다 — 제안을 자동 채택하지 말고 사용자 질문의 번호와 맞는지 확인."

§2 표:
- `kb_title_tokens` 행 삭제.
- `kb_entity_share` 행 삭제 또는 교체: "`matched`=엔티티 문자열이 제목/ID/본문 앞 1500자에 있는 건수, `total`=같은 기간·소스 전체(분모). `kb_analytics(entity=)` 와 같은 정의. share 를 인용할 때 분모·정의를 함께 쓴다."
- `kb_query` 행: "엔티티를 무시하고 전체 건수 반환" 부분을 수정 — 이제 `entity_share` 모드 출력은 `matched / total(분모)` 로 나온다. **"연월 표현은 기간 질의로 분기 안 됨"(B4)은 아직 미해결이므로 유지.**
- `kb_search(section=…)` 행: "복수형은 0건"을 삭제하고 "모르는 section 은 400+허용 목록, `checkitems` 같은 별칭은 정규화됨"으로. `area` 는 대소문자 무시로 수정, 문서에 없는 값은 `[경고]` 가 붙는다고 적는다.
- `kb_analytics(group_by=bogus)` 행 → "허용 값 밖이면 `status: invalid_request` 와 허용 목록이 나온다. 오류를 읽고 재호출"로 완화. `kb_citec_recurring_patterns` 의 틀린 도메인도 동일(대소문자 무시, 허용 목록 반환).
- `kb_graph_explore` 행: "연관 과거 장애가 앵커 4종 모두 0건"을 "섹션 이름이 `연관 failure bucket` 으로 바뀜. 0건은 등록 패턴이 연결되지 않았다는 뜻이며 과거 장애가 없다는 뜻이 아니다. 과거 장애 티켓은 `연관 문서` 의 support_history/incident_reports 항목 또는 `kb_similar_incident`"로 교체.
- `kb_match_failure_bucket` 행: 틀린 `fb_domain`/`environment` 는 이제 거부된다는 한 줄 추가(버킷 8개 한계는 그대로).
- **아직 지우지 않는 행**: `kb_ask`/`wiki_ask`(C), `kb_list_checkitems` 코드순·#EN(별건), `kb_query` 응답 크기, `kb_similar_incident` 적용성 라벨, `web_url` 치환.

### `references/wiki-mcp.md`
- 16행 근처 "`kb_entity_share`·`kb_title_tokens`는 현재 신뢰하지 않음" 삭제. 대신 "비중은 `kb_analytics` 또는 `kb_entity_share`(분모·정의 함께 인용)".
- "CITECTS 번호 → `kb_ticket`" 행에 "대소문자 변형은 서버가 해석, 숫자만은 제안만"을 한 줄 추가.
- PISA 점검항목 행: B1 은 미해결이므로 "직접 매칭 없으면 질의를 증상·파라미터명으로 변경" 주의는 **유지**.
- 검색 결과를 읽는 절에 추가: "결과 머리의 `trust=` 와 `[경고]` 줄을 먼저 본다. `trust=weak` 이거나 식별자 경고가 있으면 그 결과를 근거로 채택하지 않는다."

### `references/wiki-graph.md`
- "연관 과거 장애" 언급을 "연관 failure bucket"으로 바꾸고, "0건 = 과거 장애 없음 아님" 문장 추가. `snapshot` 설명은 "크롤 시점 저장본, 현재 버전은 confluence-mcp로 확인"으로 맞춘다.

### `references/corpus-taxonomy.md`
- 9~10행: `checkitem`(단수)·소문자 `area` 지시는 유지하되 "복수형은 조용히 0건"/"`Linux`처럼 대문자는 0건" 함정 문구를 삭제하고, "별칭·대소문자는 서버가 정규화, 모르는 section 은 허용 목록과 함께 거부됨"으로 바꾼다.

### `SKILL.md`
- 도구 선택 요약(36행)은 변경 없음. 증거 인용 원칙 2·3번에 "검색 머리의 `trust=weak` / `[경고] 질의의 식별자…` 는 근거 부족 신호"를 한 줄 덧붙인다.

### `references/workflows.md`
- 변경 없음(기존 `kb_ticket` 우선 규칙 유지).

## 3. 갱신 후 스킬 스모크 (회귀 확인용 — 운영 배포 후)

| 질문 | 기대 |
|---|---|
| "모니모 Redis 유사장애" | `kb_similar_incident` → 후보 → `kb_ticket` 원문 |
| "지난 주 지원건" | `kb_list_tickets(relative="지난 주")` |
| "Linux OOM 점검항목" | (B1 미해결) `section="checkitem", area="linux"`, 못 찾으면 증상·파라미터명으로 재질의 — 우회 유지 |
| "CITECTS-99999 내용" | `kb_ticket` 이 "찾을 수 없습니다". 검색을 쓰면 `[경고]`·`trust=weak` 가 보여야 함 |
| "최근 3일 LOOKIN 변경" | `kb_recent_changes` |
| "citects-2502 알려줘" | 첫 줄에 해석 안내 + 본문 |
| "올해 SCP 비중" | `matched <= total`, 분모·정의 명시 인용 |

## 4. 하지 말 것

- 운영 배포 확인 전에 우회 규칙을 일괄 삭제하지 않는다.
- B/C/D 에 해당하는 행(OOM 확장, pointer-only 점유, 증상 문장, 연월 질의, `kb_ask`, frame 6.1%)은 이번에 건드리지 않는다.
- 서버 저장소를 수정하거나 서버 동작을 추측으로 단정하지 않는다 — 이 문서의 표가 근거다.
