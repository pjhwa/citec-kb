# failure_bucket 다중 플러그인 확장 설계

- 작성일: 2026-08-06
- 배경: `failure_bucket`(2026-07-29 최초 설계, [2026-07-29-failure-bucket-design.md](./2026-07-29-failure-bucket-design.md))은
  네트워크 패킷 분석 스킬(`packet-capture-rca`) 하나를 위해 만들어졌다. 서비스일류화팀은 이제
  `pacemaker-tools`(Pacemaker/Corosync 클러스터 로그), `windows-tools`(Windows 이벤트로그/AD 복제) 등
  서로 다른 진단 영역의 플러그인을 계속 늘려갈 예정이며, 각 플러그인이 "확정된(가정/추정이 아닌)"
  분석 결과를 같은 `failure_bucket` 레지스트리에 등재해 서로 재사용하는 자가개선 루프를 만들고 싶어한다.
  이 문서는 그 확장을 위해 citec-kb 저장소에 적용할 변경을 정의한다.
- **이 문서를 작업할 개발환경은 이 문서가 관찰한 리포지토리 리비전과 다를 수 있다.** 아래 "현재 상태"
  절의 `file:line` 앵커는 전부 실제로 열어 확인한 뒤 다음 절의 변경을 적용할 것 — 라인 번호가
  달라졌다면 앵커 텍스트(함수/컬럼명)로 재탐색한다.

---

## 0. 결론 먼저 — 구조적 답변

**공유 레지스트리 1개 + `fb_domain` 파셋(facet)으로 간다. 플러그인별 테이블을 만들지 않는다.**

이유: `confidence` 계산(`compute_confidence`), 신호 매칭 스코어러(`rank_buckets`/`match_bucket`), 검색
노출을 위한 `Document` 미러+임베딩 파이프라인(`_index_bucket`/`bucket_draft`)이 전부 공유 기계장치다.
플러그인마다 테이블을 나누면 이 세 가지를 세 갈래로 포크해야 하고, "같은 근본원인이 여러 도메인에서
동시에 관찰되는" 상관관계(예: 네트워크 단절이 Windows 클러스터 페일오버로 이어지는 사례)를 아예
질의할 수 없게 된다. 대신 아래처럼 **도메인을 값으로 구분**하고, 매칭·목록·대시보드에서 이 값으로
필터링한다.

---

## 1. 현재 상태 관찰 (변경 전 반드시 재확인)

| 지점 | 위치 | 현재 동작 |
|---|---|---|
| 테이블 정의 | `apps/api/app/db/models.py:353-387` (`class FailureBucket`) | `protocol` 컬럼 하나로 도메인 태깅. `created_by` 컬럼 존재하나 MCP 쓰기 경로에서 채워지지 않음 |
| 생성/정제/매칭 서비스 | `apps/api/app/failure_buckets/service.py` | `create_bucket()`(L77), `refine_bucket()`(L142, `add_signal`을 `discriminating_signals`에 그대로 append), `match_buckets()`(L178, `protocol` 필터만 SQL에서 적용 후 전량 Python 랭킹) |
| 매칭 스코어러 | `apps/api/app/failure_buckets/match.py` | `signal_ratio = len(matched) / len(discriminating_signals)` (L42) — **아래 §3의 핵심 결함** |
| Document 미러 생성 | `apps/api/app/failure_buckets/draft.py` | `bucket_draft()`(L45) 가 `domain=None`으로 명시적으로 남겨두고, 주석(L77-80)에 "`taxonomy.infer_domain`이 `protocol`에서 도출한다"고 적혀 있음 |
| 도메인 추론 분기 | `apps/api/app/taxonomy.py:60-63` | `source_type == "failure_bucket"` 이면 `metadata["protocol"].lower()`를 그대로 `Document.domain`에 넣음 |
| 도메인 어휘(코퍼스 전체) | `apps/api/app/taxonomy.py:15-23` (`_DOMAIN_RULES`) | 코퍼스 전체가 공유하는 `Document.domain` 값은 **7개 고정 어휘**뿐: `os`, `dbms`, `storage`, `network`, `virtualization`, `middleware`, `cloud` |
| `area` 필터 배선 | `apps/api/app/routers/external_compat.py:397,569` | `area` 파라미터가 그대로 `Document.domain`에 매핑됨 (`domain = area.strip() or None`) |
| MCP 도구 5개 | `mcp-server/server.py:1020-1189` | `kb_register_failure_bucket`/`kb_refine_failure_bucket`/`kb_match_failure_bucket`/`kb_list_failure_buckets`/`kb_get_failure_bucket`. `created_by`를 어떤 도구도 채우지 않음 |

**중요한 기존 결함(§0 구조와 무관하게, 지금도 실재함):** `taxonomy.py:60-63`이 `protocol`(예: `"TCP"`,
`"TLS"`)을 소문자로 그대로 `Document.domain`에 넣고 있다. 이 값은 §`_DOMAIN_RULES`의 7개 어휘
(`network` 등)에 속하지 않는다. 즉 **기존 failure_bucket 행들은 이미 `kb_search(area="network")`로
찾아지지 않는다** — `area="network"`는 `Document.domain == "network"`를 찾는데 실제 저장된 값은
`"tcp"`/`"tls"`/`"http"`이기 때문이다. 이번 확장에서 이 결함도 함께 고친다(§4).

---

## 2. 스키마 변경

```
failure_buckets (변경분만)
├ fb_domain          NEW str(32) NOT NULL  -- 플러그인/진단 영역 카테고리. 값 어휘는
│                                              references/failure-bucket-domains.md 로 관리 (§5)
│                                              예: "network", "cluster", "windows"
├ protocol            (기존 컬럼 유지) -- fb_domain="network"일 때만 쓰는 하위 분류
│                                          (TCP/TLS/HTTP). 다른 fb_domain 값에는 NULL.
│                                          하위 호환을 위해 컬럼명/의미 그대로 둔다.
└ (source_plugin 컬럼은 추가하지 않는다 — §4 참고, created_by를 그대로 쓴다)
```

**마이그레이션:** 기존 행은 전부 네트워크 패킷 분석에서 나온 것이므로 `fb_domain='network'`로
백필한다. `Index(fb_domain)`, `Index(fb_domain, protocol)` 추가. 기존 `ix_failure_buckets_protocol`은
유지(네트워크 도메인 내부 하위 필터로 계속 씀).

**`fb_domain`을 `Document.domain`(코퍼스 공유 `area` 어휘)에 직접 넣지 않는다.** §1에서 확인했듯
`Document.domain`은 코퍼스 전체가 공유하는 7개 고정 어휘다. `fb_domain`은 그보다 세분화된 별도
축(플러그인 단위)이므로, `taxonomy.py`에 매핑 테이블을 하나 둬서 `fb_domain → 기존 7개 어휘 중 하나`로
변환한 값만 `Document.domain`에 넣는다:

```python
# apps/api/app/taxonomy.py
_FB_DOMAIN_TO_CORPUS_DOMAIN: dict[str, str] = {
    "network": "network",
    "cluster": "os",       # Pacemaker/Corosync는 OS/커널 계층 HA로 분류
    "windows": "os",       # Windows 이벤트로그/AD 복제도 OS 계층
    # 새 fb_domain 추가 시 이 표에도 반드시 항목을 추가한다 (§5와 함께 갱신)
}

def infer_domain(..., metadata=None):
    if source_type == "failure_bucket" and metadata:
        fb_domain = str(metadata.get("fb_domain") or "").lower()
        if fb_domain in _FB_DOMAIN_TO_CORPUS_DOMAIN:
            return _FB_DOMAIN_TO_CORPUS_DOMAIN[fb_domain]
        # 매핑 표에 없는 새 fb_domain: 코퍼스 도메인은 비워두고(None) 아래
        # 키워드 규칙(_DOMAIN_RULES)으로 폴백 — 완전히 없애지 않는다
    ...  # 기존 로직 계속
```

이렇게 하면 `kb_search(area=)`/`kb_query`의 코퍼스 전역 도메인 필터링은 항상 기존 7개 어휘로만
동작하고, `fb_domain`은 failure_bucket 전용 필터(`kb_list_failure_buckets`, `kb_match_failure_bucket`,
운영 대시보드)에서만 쓰이는 별도 파셋이 된다. **기존 결함(잘못된 `tcp`/`tls` 값)도 이 변경으로
같이 고쳐진다** — 백필 시 `protocol` 값이 아니라 `fb_domain='network'`가 매핑 테이블을 거쳐
`domain='network'`로 올바르게 재계산되도록 `documents` 미러 재인덱싱을 포함한다.

---

## 3. 매칭 스코어러 수정 — 성숙한 버킷이 자기 자신을 깎아먹는 문제

**현상:** `match.py:42-44`

```python
total = len(discriminating) or 1
signal_ratio = len(matched) / total
score = 0.6 * signal_ratio + 0.4 * float(bucket.get("confidence") or 0.5)
```

`refine_bucket()`(`service.py:155-157`)은 확인(confirm)마다 `add_signal`을 `discriminating_signals`에
그대로 append한다. 즉 **신호가 쌓일수록 분모가 커진다.** 신호 2개짜리 신생 버킷이 2개 다 맞으면
`signal_ratio=1.0`인데, 신호 12개짜리로 잘 정제된 버킷이 3개만 맞아도(부분 매칭은 원래 정상 동작)
`signal_ratio=0.25`로 떨어져 `가능` 대신 `조건부`/`비권고`로 밀린다. 자가개선 루프가 돌수록 그
버킷의 실사용 랭킹이 나빠지는 역설이다. 플러그인이 늘어나면 "신호를 몇 개 쓰는 습관이냐"에 따라
버킷 순위가 좌우되는 형평성 문제도 된다.

**수정:** 분모에 상한 `K`(기본 4)를 둔다 — 아래처럼 `signal_ratio`만 교체하고 나머지 로직은 그대로
둔다:

```python
_SIGNAL_RATIO_CAP = 4  # 절대 매칭 개수를 보상, 신호 목록이 커져도 분모가 무한정 늘지 않게 함

def match_bucket(observed_signals, symptom, bucket):
    ...
    total = len(discriminating) or 1
    signal_ratio = min(len(matched), _SIGNAL_RATIO_CAP) / min(total, _SIGNAL_RATIO_CAP)
    ...
```

이 값은 상수로 두되 `apps/api/app/failure_buckets/match.py` 상단에 이름 붙여 선언하고, 조정이
필요해지면(실사용 데이터 축적 후) 이 상수만 바꾸면 되게 한다. 더 정교한 대안(`discriminating_signals`를
"핵심 신호"/`corroborating_signals`를 "보강 신호"로 분리해 분모를 핵심 신호에만 고정하는 방식)은
플러그인이 많아지고 버킷당 신호 수가 실제로 문제가 될 때 후속 설계로 남겨둔다 — 지금은 최소 변경으로
악화를 막는 쪽을 택한다.

---

## 4. 근거 없는 등록 방지 — `evidence_ref` 필드 추가

**현상:** `evidence_grade`는 `draft.py:75`에서 모든 행에 `"machine"`으로 고정되며, 등록 요청 어디에도
"이 신호가 실제로 어떤 원자료에서 나왔는가"를 남기는 필드가 없다. `packet-capture-rca` 스킬은
프롬프트 수준에서 "지어내지 말 것"을 강제하지만, 이는 스킬 작성자의 신중함에만 의존하는 사회적
장치다. 플러그인이 여러 팀/여러 작성자로 늘어나면 이 보장이 깨진다.

**변경:** `POST /v1/failure-buckets`(`create_bucket()`)에 필수 파라미터 `evidence_ref: str` 추가.
빈 문자열이면 400 거부(다른 필수 필드와 동일한 검증 패턴). 사람이 나중에 원자료를 열어 확인할 수
있는 **구체적 포인터**만 허용한다 — 자유 서술 금지:

- 지원 티켓: `CITECTS-2481`
- Confluence 페이지: `confluence:2412784426`
- 패킷 캡처: `capture:2026-08-06-upload01.pcapng#frame=4821`
- Windows 이벤트: `evtx:DC01-System.evtx#EventRecordID=88213`
- Pacemaker 로그: `log:node1/corosync.log#L4021-L4055`

DB에 `evidence_ref: Text NOT NULL` 컬럼 추가, `bucket_draft()`의 `body_md`/`metadata`에도 포함시켜
운영 대시보드와 검색 결과에 노출한다. **승인 워크플로(draft→review→approved)는 다시 두지 않는다**
— 최초 설계(§7, out of scope)의 판단을 유지하되, "포인터가 있는가"만 등록 시점에 기계적으로
검증한다.

---

## 5. 도메인 어휘 거버넌스

`fb_domain` 값은 각 플러그인 작성자가 즉흥적으로 짓지 않도록 `references/failure-bucket-domains.md`
(신규 파일)에서 관리한다. 표 형식:

| fb_domain | 설명 | 소유 플러그인 | 코퍼스 domain 매핑(§2) | 추가일 |
|---|---|---|---|---|
| `network` | 네트워크 패킷/전송계층 장애 | packet-capture-rca | network | 2026-07-29 |
| `cluster` | Pacemaker/Corosync HA 클러스터 | pacemaker-tools | os | (신설 시 기입) |
| `windows` | Windows 이벤트로그/AD 복제/WSFC | windows-tools | os | (신설 시 기입) |

새 플러그인이 새 `fb_domain`을 쓰려면 이 표에 행을 추가하는 PR과 `taxonomy.py`의
`_FB_DOMAIN_TO_CORPUS_DOMAIN` 매핑 추가를 함께 한다(§2). **하드 제약(DB enum)은 두지 않는다** —
운영 마찰을 줄이기 위한 소프트 컨벤션이다. 대신 `create_bucket()`이 `fb_domain`이 이 표에(캐시된
목록으로) 없으면 등록은 허용하되 응답 문자열에 경고를 덧붙인다:

```
등록됨: ... (경고: fb_domain='xyz'는 references/failure-bucket-domains.md에 없는 새 값입니다.
오타가 아니라면 문서에 추가해 주세요.)
```

이 표는 §7(플러그인 개발 지침)에서도 동일하게 참조한다 — 두 문서가 같은 어휘 목록을 본다.

---

## 6. 중복 등록 완화 — `possible_duplicate_of`

여러 플러그인/작성자가 독립적으로 버킷을 등록하다 보면 실질적으로 같은 패턴을 이름만 바꿔 중복
등록할 위험이 (기존에도 있었지만) 커진다. `create_bucket()` 내부에서 insert 직전에
`match_buckets(observed_signals=discriminating_signals, symptom=symptom, fb_domain=fb_domain)`을
한 번 호출해, 최고 점수 기존 버킷이 임계값(예: `score >= 0.75`) 이상이면 응답에
`possible_duplicate_of: {bucket_id, bucket_name, confidence}`를 포함시킨다. **등록 자체는 막지
않는다**(승인 게이트를 두지 않는다는 §7 원 설계 방침 유지) — 호출한 플러그인/에이전트가 이 필드를
보고 판단하게 한다(§7 플러그인 지침에도 이 응답 처리 규칙을 명시).

---

## 7. Provenance — 새 컬럼 대신 기존 `created_by` 배선

`created_by`(`models.py:381`)는 이미 존재하고 `create_bucket()`도 이미 인자로 받지만(`service.py:86,97`),
`kb_register_failure_bucket`(`server.py:1020-1050`)이 요청 바디에 넣지 않아 항상 `NULL`로 저장된다.
**새 컬럼(`source_plugin` 등)을 추가하지 않는다** — 기존 필드를 채우기만 하면 된다. MCP 도구 시그니처에
파라미터 하나만 추가:

```python
async def kb_register_failure_bucket(
    ...,
    fb_domain: str,               # 신규, 필수 — §2/§5
    evidence_ref: str,             # 신규, 필수 — §4
    source_plugin: str = "",       # 신규, 권장 — 예: "pacemaker-tools@1.0.0"
) -> str:
    body = {..., "fb_domain": fb_domain, "evidence_ref": evidence_ref,
             "created_by": source_plugin.strip() or None}
```

`kb_refine_failure_bucket`에도 선택 파라미터 `source_plugin`을 추가해 정제 호출자도 남길지는
선택 사항으로 둔다(v1은 등록 시점만 필수로 강제).

---

## 8. API/도구 시그니처 변경 요약

| 도구/엔드포인트 | 변경 |
|---|---|
| `POST /v1/failure-buckets` | `fb_domain`(필수), `evidence_ref`(필수), `created_by`(선택, MCP에서 `source_plugin`으로 전달) 추가. 응답에 `possible_duplicate_of`(선택) 추가 |
| `POST /v1/failure-buckets/match` | `fb_domain`(선택 필터) 추가, SQL `WHERE`에 반영(§9) |
| `GET /v1/failure-buckets` | `fb_domain`(선택 필터) 추가 |
| `kb_register_failure_bucket` | `fb_domain`, `evidence_ref` 필수 파라미터 추가, `source_plugin` 선택 파라미터 추가 |
| `kb_match_failure_bucket` | `fb_domain` 선택 파라미터 추가 |
| `kb_list_failure_buckets` | `fb_domain` 선택 파라미터 추가 |
| `kb_get_failure_bucket` / `kb_refine_failure_bucket` | 변경 없음 (bucket_id로 동작) |
| `kb_tools_help()` | 위 시그니처 변경 반영 (서버 L860-864 목록 갱신) |

---

## 9. 조회 성능 — SQL 필터를 Python 랭킹보다 먼저

`match_buckets()`(`service.py:178-191`)는 이미 `protocol` 필터를 SQL에서 거는데, `fb_domain`도
같은 자리에 추가한다(`stmt.where(FailureBucket.fb_domain == fb_domain)`). 플러그인이 늘어나
버킷 수가 수백~수천 단위가 되면 전량 조회 후 Python 랭킹은 느려진다 — 이번 변경에서
`stmt.limit(500)`(안전판, 상수로 이름 붙여 선언) 정도만 추가하고, 그 이상 커지면 pgvector 기반
사전 후보 축소를 후속 과제로 남긴다(범위 밖으로 명시).

---

## 10. 문서/스크립트 갱신 대상

- `README.md`, `docs/MCP.md`, `docs/EXTERNAL_API.md` — 신규 파라미터 반영
- `docs/AI_AGENT_GUIDE.md` §4.15 — `fb_domain`/`evidence_ref` 필수화 반영, "다중 플러그인" 문구 추가
- `docs/PACKET_ANALYSIS_MCP_GUIDE.md` — `fb_domain="network"` 고정값 사용 예시 추가(하위 호환:
  이 스킬은 계속 `protocol`도 함께 채운다)
- `references/corpus-taxonomy.md` §4 — `fb_domain` 필드와 코퍼스 `domain` 비분리 설명 추가
- `references/failure-bucket-domains.md` — 신규(§5)
- Alembic 마이그레이션 1개: `fb_domain` 컬럼 추가+백필(`'network'`), `evidence_ref` 컬럼 추가(기존 행은
  `'legacy:pre-migration'` 등 명시적 placeholder로 백필 — 빈 문자열 금지, 신규 검증 규칙과 일관되게),
  인덱스 추가
- `mcp-server/test_smoke.py` — 신규 필수 파라미터 반영한 스모크 테스트 갱신
- `apps/api/app/ops/dashboard.py` — "최근 등록된 failure_bucket" 위젯에 `fb_domain`/`created_by`
  열 추가, 도메인별 건수 브레이크다운 추가(플러그인이 늘어날수록 운영 가시성에 필요)

---

## 11. 범위 밖 (Out of scope)

- `discriminating_signals`를 core/corroborating으로 분리하는 더 정교한 매칭 재설계(§3의 대안) —
  캡 상수(K=4)로 충분히 완화된 이후, 실사용 데이터가 쌓여 여전히 문제면 후속 설계.
- 버킷 병합/삭제 API(`possible_duplicate_of`를 본 뒤 사람이 실제로 정리할 수단) — 지금은 경고만
  띄우고 정리는 수동(운영 대시보드에서 확인 후 직접 DB 조치)으로 남긴다.
- `fb_domain` 값에 대한 DB-level enum 제약, 쓰기 인증/권한 분리, rate limit — 최초 설계(§7)와 동일한
  이유로 계속 범위 밖.
- pgvector 기반 매칭 사전 후보 축소(§9) — 버킷 수가 실제로 문제될 때 재검토.
