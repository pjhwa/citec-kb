# Confluence 맵 (구조 전용 인덱스) — `confluence_map`

`docs/CONFLUENCE_SYNC.md`(전문 본문 동기화, LOOKIN/TechRepo 특정 서브트리 한정)의
자매 기능. citec-kb가 **본문 전체를 저장·임베딩하는 서브트리는 여전히
LOOKIN(confluence_docs) 4개·TechRepo(tech_repo) 5개로 한정**이다. 그와 별개로,
부서원이 실제로 참고하는 Confluence 공간 전체 — LOOKIN·TechRepo의 전체
공간은 물론 서비스일류화팀, SCP Cloud Umbrella Team(ICLOUDUT), SCP인프라운영팀,
OPENSTACK PLATFORM, MSP인프라기술그룹, 통합Managed Infra서비스팀,
기술검증그룹까지 — **제목·URL·경로(breadcrumb)만 담은 가벼운 포인터**로
넣어서, wiki-mcp만 연결한 사람도 "이 주제는 어느 공간/어느 페이지에 있는지"
검색으로 바로 찾을 수 있게 한다.

## 왜 본문을 안 넣나

- 샘플링 결과(2026-09-16), 여러 공간의 KDB성 문서는 **제목 자체가 이미 증상/
  에러 문자열**이다 — 예: CLDENG "문제 해결 문서(KDB)"의
  `[HW] CPU Uncorrectable Machine Check Exception`,
  `[HV] VMware : (VCENTER) Alarm ... changed from Green to Yellow`. 제목만
  인덱싱해도 키워드 검색 가치가 크다.
- 신규 5개 공간은 본문까지 전부 넣으면 CI-TEC 소관이 아닌 다른 조직의
  손익관리/팀KPI/해외법인업무 같은 문서까지 검색 결과에 섞여 노이즈가
  커진다.
- `evidence_grade="C"`로 낮게 매겨서, 같은 주제의 실제 A등급 confluence_docs/
  tech_repo 문서가 있으면 그게 항상 우선한다 — 맵 항목은 "포인터"이지
  "근거"가 아니다. 그래서 LOOKIN/TechRepo처럼 이미 A등급 본문이 들어간
  페이지도 맵에 C등급 포인터로 중복 등록되는 걸 그냥 허용한다(아래
  "설계 변경 이력" 참고) — 검색 랭킹에 해가 없다.

## 범위 (2026-09-16 승인, 박재화 — 2차 결정으로 갱신)

| 공간 | space_key | 방식 | 크롤 대상 |
| --- | --- | --- | --- |
| CI-TEC | LOOKIN | **전체 공간, 매일 증분** | 공간 홈(222532724) 전체(약 7,296건) |
| TechRepo | TechRepo | **전체 공간, 매일 증분** | 공간 홈(31951116) 전체(약 3,910건) |
| 서비스일류화팀 | ServiceExcellenceTeam | **전체 공간, 매일 증분** | 공간 홈(2001257717) 전체(499건) |
| Cloud Umbrella Team | ICLOUDUT | **전체 공간, 매일 증분** | 공간 홈(230553963) 전체(약 14,600건) |
| SCP인프라운영팀 | DevOps001 | **승인된 서브트리만, 매일 증분** | § 아래 |
| OPENSTACK PLATFORM | Openstack101 | **승인된 서브트리만, 매일 증분** | § 아래 |
| MSP인프라기술그룹 | CLDENG | **승인된 서브트리만, 매일 증분** | § 아래 |
| 기술검증그룹 | DFTRTS | **승인된 서브트리만, 매일 증분** | § 아래 |
| 통합Managed Infra서비스팀 | EMCloud | **승인된 서브트리만, 매일 증분** | § 아래 |
| MSP인프라운영팀 | sysops | **제외** | 홈 하위 2개뿐, 이슈/KDB 성격 서브트리 없음(2026-09-16 확인) — 생기면 추가 |

**전부 `MAP_SOURCE_DEFS`에 등록되어 매일 1회(운영 크론, 등록은 별도 승인
필요) 증분 갱신되는 것을 목표로 한다.** LOOKIN/TechRepo/서비스일류화팀/
ICLOUDUT 4곳은 "전체 공간"이 곧 root 하나(공간 홈페이지)의 전체 자손이라는
뜻이고, LOOKIN/TechRepo는 이미 confluence_docs/tech_repo로 본문 전체가 들어간
페이지들도 이 맵에 겹쳐서 포인터로 등록된다(의도된 중복, 위 "왜 본문을
안 넣나" 참고).

### 설계 변경 이력 (참고용)

최초 설계(2026-09-16 1차)는 LOOKIN/TechRepo에 대해 "confluence_docs/
tech_repo에 이미 들어간 서브트리를 제외한 나머지만, 스킬 크롤 데이터로
1회성 마이그레이션"이었다. 이후 박재화가 "전체 공간을 맵에 상시 포함하고
매일 갱신"으로 범위를 넓혀서(2차 결정) 지금 형태가 됐다 — 1회성 스냅샷의
근본 문제(예: 스킬 크롤 시점과 실제 사이 새로 생긴 326건 격차) 자체가
"매일 실시간 재크롤"로 바뀌면서 사라진다.

## 승인된 서브트리 (신규 5개 공간 — 전체 공간이 아니라 선별)

각 공간의 실제 콘텐츠를 `getChild`로 샘플링해 CI-TEC 장애/기술지원 관련
서브트리만 선별했다 — 손익관리/팀KPI/해외법인업무/Personal Space/999.
FreeSpace 등 다른 조직 자료나 잡동사니는 제외. (LOOKIN/TechRepo/
서비스일류화팀/ICLOUDUT은 위 표대로 전체 공간이라 이 선별이 해당 없음.)

- **DevOps001**: `005. 이슈/문제/KDB/SOP`, `006. SCP CASE study`,
  `★★ SCP (SCP SRE + SCP NW Share) ★★`
- **Openstack101**: `knowledge base`, `9. 팀 ISSUE 관리`, `Nuri 운영구성`,
  `Nuri 운영 관련`
- **CLDENG**: `KB/SOP 검색`, `문제 해결 문서 (KDB)`, `004. 이슈,장애 관리`,
  `006. 기술&자동화`, `007. HW 운영(서버HW/가상화/스토리지)`, `009. 통합백업`,
  `119. DR 전환 및 비상가동 절차`
- **DFTRTS**: `기술자료`, `인프라설계검증`, `하드웨어분석`
- **EMCloud**: `문제 해결 문서`, `7. Cloud Engineering(Shared Service)`

정확한 pageId는 `apps/api/app/confluence/map_sync.py`의 `MAP_SOURCE_DEFS`
참고(코드가 최신 출처).

## 사용법

### 1) 마이그레이션 (부트스트랩, LOOKIN/TechRepo/ServiceExcellenceTeam/ICLOUDUT)

Confluence 접근이 필요 없다 — citec-mcp-workbench 스킬이 이미 크롤링해 둔
jsonl 인덱스를 재구성만 한다. 목적은 "라이브 크롤러의 첫 실행이 이 4개 공간
전체(~26,000건)를 처음부터 다시 크롤하지 않게" 하는 것 — 파일만 미리
채워두고 커서를 시드한다.

```bash
# 1단계: 파일 작성 (의존성 불필요, 어느 호스트에서든 실행 가능)
python scripts/migrate_confluence_map_from_skill_index.py \
    --skill-refs ~/.claude/skills/citec-mcp-workbench/references \
    --raw-dir data/raw \
    --dry-run   # 먼저 건수만 확인
python scripts/migrate_confluence_map_from_skill_index.py --raw-dir data/raw

# 2단계: DB 적재 (citec-kb 앱/DB가 실제로 있는 곳에서)
python -m app.ingest.cli --raw-dir data/raw --sources confluence_map

# 3단계: 커서 시드 (앱 환경 필요 - SQLAlchemy) - 다음 라이브 크롤이
# 이 스냅샷 이후 변경분만 묻도록. 반드시 2단계 이후에 실행.
python scripts/migrate_confluence_map_from_skill_index.py --seed-cursors-only
```

2026-09-16 dry-run 실측(참고용, 공간 규모가 바뀌면 달라짐):

```json
{
  "written": 26305,
  "by_space": {"LOOKIN": 7296, "ServiceExcellenceTeam": 499, "TechRepo": 3910, "ICLOUDUT": 14600}
}
```

이제 모든 row를 조건 없이 쓴다(이전 버전은 confluence_docs/tech_repo에 이미
들어간 서브트리를 걸러냈으나, 위 "설계 변경 이력" 대로 전체 공간을 상시
포함하기로 하면서 그 필터링이 무의미해져 제거했다).

`--seed-cursors-only`가 세팅하는 시각(대략치, 스킬 크롤 실행일 기준):

| source_id | 시드 시각(UTC) | 근거 |
| --- | --- | --- |
| confluence_map_lookin | 2026-07-23 | confluence-pages.jsonl 크롤일 |
| confluence_map_techrepo | 2026-07-23 | 〃 |
| confluence_map_serviceexcellenceteam | 2026-07-23 | 〃 |
| confluence_map_icloudut | 2026-07-30 | confluence-pages-scp.jsonl 크롤일 |

생성된 `data/raw/confluence_map/*.md`는 `scripts/out.sh --data`로 번들링해
운영 서버로 옮기고, `scripts/in.sh --data`로 반영한다.

### 2) 라이브 증분 크롤 (전체 9개 source_id 공통, 운영 서버에서만 — dev는
Confluence 접근 불가)

```bash
# 1. 반드시 먼저: 파일 쓰기만, DB 반영 없음. 루트 하나만, 소량으로.
python -m app.confluence.map_sync_cli --dry-run --root-id 601879661 --max-pages 5

# 2. 문제 없으면 전체 dry-run (9개 source_id 전체 bootstrap 규모 확인 -
#    LOOKIN/TechRepo/ServiceExcellenceTeam/ICLOUDUT은 위 마이그레이션+커서
#    시드를 먼저 했다면 "신규/변경분만"이라 작은 규모여야 정상)
python -m app.confluence.map_sync_cli --dry-run

# 3. 실제 반영
python -m app.confluence.map_sync_cli
```

옵션은 `app.confluence.sync_cli`와 동일한 이름/의미
(`--source-ids`/`--max-pages`/`--root-id`/`--raw-dir`/`-v`) — 차이는
`--sources` 대신 `--source-ids`(공간별로 독립 커서를 갖기 때문에
source_type이 아니라 source_id 단위로 선택).

**마이그레이션+커서 시드를 안 하고 바로 라이브 크롤을 돌리면** LOOKIN
7,296건 + TechRepo 3,910건 + ServiceExcellenceTeam 499건 + ICLOUDUT
14,600건을 전부 처음부터 크롤한다 — 0.3req/s 기준 약 10시간 소요 추정.
마이그레이션을 먼저 하는 걸 강력히 권장한다.

### 레이트리밋 — `CONFLUENCE_RATE_LIMIT_RPS`는 절대 올리지 않는다

`map_sync.py`는 `app.confluence.sync`(기존 confluence_docs/tech_repo,
이미 운영 중인 크론)와 **같은 환경변수(`CONFLUENCE_RATE_LIMIT_RPS`, 기본
0.3)와 같은 Confluence 계정**을 공유한다. 이 계정은 담당자 본인의 대화형
브라우징·MY-OS 등 다른 자동화 도구와도 공유되고, 실제로 429(레이트리밋)를
받은 사례가 있어(`docs/CONFLUENCE_SYNC.md` 참고) 기본값을 0.3까지 낮춰둔
것이다. map_sync 크롤을 빠르게 하려고 이 값을 올리면 confluence_docs/
tech_repo 크론이나 다른 도구까지 같이 영향을 받는다 — **속도가 급하다는
이유로 올리지 않는다.** 429/503은 `ConfluenceClient`가 `Retry-After` 헤더
기반 백오프로 자동 처리하므로, 신규 5개 공간처럼 서브트리 규모가 작은
크롤은 기본값으로도 실용적인 시간 안에 끝난다.

## 기존 confluence_docs/tech_repo 크롤(`sync.py`)과의 차이

| | `app.confluence.sync` | `app.confluence.map_sync` |
| --- | --- | --- |
| 저장 내용 | 본문 전체(clean text) | 제목 + URL + 경로만 |
| source_type | confluence_docs / tech_repo | confluence_map (전부 동일) |
| space_key | source_type당 1개 고정, 서브트리만 | source_id(공간)당 1개, 독립 커서 — 4곳은 전체 공간, 5곳은 서브트리만 |
| evidence_grade | A | C (포인터, 근거 아님) |
| 페이지당 API 호출 | `get_page_full`(body 포함) | `get_page_meta`(body 없이 version+ancestors만) |
| 크론 | 이미 운영 등록(매일 12시) | **미등록** — 담당자 승인 후 별도 등록 (목표는 동일하게 매일 1회) |

## 실측 버그 및 수정 (2026-09-16 운영 dry-run)

`--dry-run`(9개 source_id 전체) 실행 중 첫 source_id(`confluence_map_lookin`)의
검색 호출이 401을 받자 **그 즉시 전체 프로세스가 Traceback과 함께 죽었다** —
나머지 8개 source_id(techrepo/serviceexcellenceteam/icloudut/신규 5개)는
아예 실행도 안 됐다. 원인: 페이지네이션 루프에서 개별 페이지 조회
(`get_page_meta`)만 try/except로 감싸져 있었고, root별 검색/목록 조회
(`search_pages_incremental`) 자체는 보호돼 있지 않았다 — 자매 모듈
`app.confluence.sync`(confluence_docs/tech_repo, 이미 운영 크론)를 그대로
본떠 만들었기 때문에 그쪽에도 동일한 결함이 있었다(`docs/CONFLUENCE_SYNC.md`
참고, 같이 수정함). 이제 검색 호출도 root 단위로 try/except로 감싸 실패 시
그 root만 포기하고(`errors`에 기록) 다음 root/source_id로 계속 진행한다.

## 알려진 한계

- 삭제/비공개 전환 페이지는 탐지하지 않는다(기존 `sync.py`와 동일 한계).
- sysops(MSP인프라운영팀)는 현재 제외 — 이슈/KDB 성격 서브트리가 생기면
  `MAP_SOURCE_DEFS`에 추가.
- LOOKIN/TechRepo는 confluence_docs/tech_repo와 confluence_map에 같은
  페이지가 두 번(A등급 본문 + C등급 포인터) 들어간다 — 검색 랭킹엔 무해하지만
  결과 개수/UI엔 중복으로 보일 수 있다. 필요하면 정리(dedup) 스크립트를
  추가할 수 있다 — 아직 만들지 않았고, 승인 없이는 만들거나 실행하지 않는다.
- 크론 등록·배포는 하지 않았다 — 담당자(박재화) 승인 후 `scripts/out.sh`/
  `in.sh`로 배포하고 `confluence_sync.sh`와 같은 방식(목표: 매일 1회)으로
  크론 등록.
- **권한 검증 없음 (아래 "이창호 리뷰(v0.2) 반영" §권한 참고).** 제목·URL·
  경로만 노출하고 본문은 없지만, 조회자별 Confluence 읽기 권한 확인은
  하지 않는다 — 박재화 승인(2026-09-16)으로 이번 범위(제목/URL/경로만,
  본문 없음)에서는 이 상태로 진행하기로 확정했다. 권한 검증 모델 도입은
  후속 과제(아래 참고).

## 이창호 리뷰(v0.2) 반영 — Confluence 문서 지도 및 citec-kb 구현 설계

2026-09-16, 이창호 프로가 별도로 훨씬 포괄적인 차기 설계안(v0.2)을
Confluence 문서로 제출했다
(`https://devops.sdsdev.co.kr/confluence/x/_XRVlw`, pageId `2538960121`,
첨부 zip `confluence-map-handoff.zip`에 OpenAPI 계약·백로그·평가 seed·개념도
포함). 실제 코드는 없고 설계 문서 + mock 계약만 있다. 이번 라운드에서 이미
구현·실행한 것과 비교해 반영 여부를 정리한다.

### 이미 일치하는 방향

- KB 저장은 "거대한 markdown 지도 하나"가 아니라 페이지별 구조화 레코드 —
  confluence_map도 페이지 1건당 문서(레코드) 1개.
- 첫 버전에서 전량 LLM 요약·신규 그래프DB·강제 벡터DB 도입은 제외 —
  confluence_map도 순수 메타데이터(제목/URL/경로)만, 요약/임베딩 새 인프라
  없음.
- 기존 kb_query/kb_search/kb_ask의 검색 모집단은 그대로 유지 — confluence_map은
  기존 컬렉션에 새 source_type만 추가하는 방식이라 기존 근거 코퍼스를
  건드리지 않는다.

### 반영 안 함 (범위 밖으로 확정, 2026-09-16 박재화 승인)

| 리뷰 권고 | 왜 지금 안 하는가 |
| --- | --- |
| 조회자별 Confluence 실시간 권한 검증(§6 "권한 모델 결정") | 제목/URL/경로만 노출(본문 없음)이라 노출 수준이 제한적 — 이번 범위에서는 문서화만 하고 실제 검증 로직은 후속 과제로 미룬다. **본문을 저장하는 confluence_docs/tech_repo로 확장할 계획이 생기면 이 결정을 반드시 재검토해야 한다** — 그때는 노출 수준이 달라진다. |
| map_sources/map_pages/map_page_search/map_sync_runs/map_work_items/map_publications 같은 별도 스키마 | 기존 `documents`/`sources` 테이블 + `confluence_map` source_type으로 충분히 MVP를 만족. 재설계는 실제 검색 품질 이슈가 나오면 그때 판단. |
| 신규 조회 API 4종(`/api/confluence-map/*`) + MCP 4종(`kb_confluence_map_*`) | 기존 `kb_search`/`kb_query`가 이미 confluence_map 문서를 검색 모집단에 포함해 제공 — 별도 API/MCP 계층은 후속 과제. |
| 소제목/발췌(excerpt) 기반 검색 단서 | confluence_map은 제목+경로만 저장 — 소제목 추출은 본문을 읽어야 하는데, 이번 설계는 의도적으로 본문을 안 읽는다(§ "왜 본문을 안 넣나"). |
| 일일 변경 본문 diff + 별도 "전체 메타데이터 대조" 배치 | confluence_map은 매일 전체 서브트리를 `ancestor=` CQL로 다시 훑는 단순한 방식이라, 리뷰가 말하는 "증분+대조 이중 구조"보다 단순하다 — 이동/라벨 변경 탐지가 그만큼 늦을 수 있다는 한계는 인정하고 넘어간다. |

### 후속 검토로 남겨둔 것 (2026-09-16 박재화 확정)

1. **신규 발견 공간 3곳(SPC, GUID, genaibusiness)** — 이창호 리뷰가 실시간
   조회로 확인한 GitHub 관련 문서 6건이 전부 기존 9개 공간 밖에 있었다.
   이번 범위(오늘 밤 마이그레이션+5개 신규 공간 크롤)에서는 제외하고,
   추후 `getChild`로 서브트리 샘플링 후 루트 선정 절차를 그대로 밟는다.
2. **스킬 jsonl 스냅샷의 "seed_only" 취급** — 리뷰는 과거 크롤 데이터를
   "검증 전 후보"로만 쓰고 현재 상태로 오인하지 말라고 권고한다.
   `scripts/migrate_confluence_map_from_skill_index.py`는 지금 이 구분 없이
   바로 확정 데이터로 적재한다 — 커서 시드 이후 매일 라이브 크롤이 실제
   상태로 자연 보정되므로 실질적 영향은 제한적이지만, 스키마에
   seed_only/verified 같은 상태 필드를 추가하는 건 하지 않았다.
3. **권한 모델** — 위 표 참고. confluence_docs/tech_repo로 확장 시 재검토
   필수.

원본 문서와 첨부 6개 파일(구현계획/API계약/백로그/평가seed/검토 3편)은
Confluence 페이지에 그대로 남아 있다 — 이 표는 그 문서의 요약과 반영 여부
판단이지, 원문을 대체하지 않는다.

## 코드/스크립트 위치

- `apps/api/app/confluence/client.py` — `get_page_meta`(신규, body 없이 조회).
- `apps/api/app/confluence/map_sync.py` — `MAP_SOURCE_DEFS`(9개 source_id),
  크롤 오케스트레이션, 프론트매터 빌더, 커서 관리(공간별 독립),
  `seed_cursor()`(마이그레이션 후 커서 시드용).
- `apps/api/app/confluence/map_sync_cli.py` — CLI.
- `apps/api/app/ingest/adapters.py` — `iter_confluence_map` (source_type
  등록 포함).
- `scripts/migrate_confluence_map_from_skill_index.py` — 부트스트랩
  마이그레이션 + 커서 시드(`--seed-cursors-only`).
- 단위테스트: `apps/api/tests/test_confluence_map_sync.py` — **작성만 하고
  이 세션에서는 실행하지 못했다**(이 dev 호스트에 sqlalchemy 등 의존성이
  없고, pip install이 사내망 SSL 인증서 문제로 막힘). 로직은 수동으로
  추적 검증했지만(그리고 이미 검증된 `test_confluence_sync.py` 패턴을 그대로
  따름), 실제 `pytest`/CI 통과는 운영자가 의존성이 설치된 환경(Docker
  컨테이너 또는 `.venv`)에서 한 번 돌려 확인해야 한다.
