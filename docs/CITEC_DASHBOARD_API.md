# CI-TEC 반복장애 대시보드 API/MCP (`citec_dashboard`)

CI-TEC(각 컴포넌트 — Linux/Windows/VMware/OpenStack/Kubernetes/Middleware/
Network/Storage/Ceph/Database — 전문가가 장애원인분석을 지원하는 부서) 관점에서
SWIM(`incident_reports`) 데이터의 **반복 장애 패턴**을 조회하는 읽기 전용
API/MCP 계층이다. `~/dev/citec-dashboard`(별도 프로젝트, CI-TEC 인력의
Jira/Confluence 활동을 보여주는 기존 대시보드)나 다른 어떤 클라이언트에서도
이 API를 호출해 시각화를 만들 수 있다 — **citec-kb 쪽 구현 범위는 API/MCP/문서까지이고,
citec-dashboard 쪽 화면 구현은 이 문서의 범위 밖이다** (2026-09-16 확정).

## 목차

1. [배경과 설계](#1-배경과-설계)
2. [데이터 파이프라인](#2-데이터-파이프라인)
3. [핵심 개념](#3-핵심-개념)
4. [배치 job — citec_domains/severity_tier 채우기](#4-배치-job--citec_domainsseverity_tier-채우기)
5. [REST API 레퍼런스](#5-rest-api-레퍼런스)
6. [MCP 도구 레퍼런스](#6-mcp-도구-레퍼런스)
7. [알려진 한계 / 설계상 트레이드오프](#7-알려진-한계--설계상-트레이드오프)
8. [citec-dashboard(또는 다른 클라이언트) 연동 가이드](#8-citec-dashboard또는-다른-클라이언트-연동-가이드)
9. [운영 배포 체크리스트](#9-운영-배포-체크리스트)
10. [코드 위치](#10-코드-위치)

---

## 1. 배경과 설계

CI-TEC는 SWIM(전사 장애관리시스템, `incident_reports` source_type, 2026-09-16
기준 15,333건, 2009~2026년)을 컴포넌트별 반복 장애 패턴 관점에서 살펴보고
싶어한다. 이 기능을 만들기까지의 실제 데이터 분석·설계 결정 과정(citec-kb 세션
대화 기록)에서 확인된 핵심 사실:

- **SWIM은 전사 시스템이지 CI-TEC 전용이 아니다.** 전체 15,333건 중 58.8%가
  단순 "Network"(주로 영업/생산 사이트 회선 장애)이고, CI-TEC의 10개 전문
  도메인에 실제로 걸리는 비율은 도메인별로 0.1%~6.8% 수준이다. 그래서 이
  API의 모든 집계는 **CI-TEC 도메인 태그가 하나라도 붙은 문서만** 대상으로
  한다 — 태그가 없는 문서(순수 일반 회선 장애 등)는 애초에 집계에 안 잡힌다.
- **반복 여부는 여러 축(도메인/부서/고객사/시간/심각도)으로 봐야 의미가 있다.**
  단일 "몇 번 반복됐나"보다 "어느 팀이 자주 겪는가", "여러 고객사에 걸친
  공통 문제인가", "최근에도 계속되는가", "심각도는 어느 정도인가"를 조합해야
  실제 액션 아이템이 나온다 — 그래서 `/recurring-patterns`는 고정된 패널이
  아니라 **차원 조합을 자유롭게 고르는 쿼리 API**로 설계했다.
- **키워드 기반 태깅은 완벽하지 않다.** 실제 코퍼스 검증 중 "AD서버"라는
  부분 문자열만으로 무관한 장애를 Windows로 잘못 태깅한 사례를 발견해
  수정했다(§7 참고) — 태깅 결과를 "확정된 사실"이 아니라 "1차 신호"로
  다루는 게 맞다.

## 2. 데이터 파이프라인

```
data/raw/incident_reports/*.md  (SWIM 원본, 별도 파이프라인이 채움 — 이 기능의 범위 밖)
        │  app.ingest.cli --sources incident_reports
        ▼
documents 테이블 (source_type='incident_reports')
        │  app.frames.cli --citec-domains --source-type incident_reports   ← §4
        ▼
issue_frames 테이블 (citec_domains ARRAY, severity_tier)
        │  app.citec_dashboard.service (읽기 전용 쿼리)                     ← §5
        ▼
GET /v1/citec-dashboard/*  ──▶  MCP kb_citec_*  ──▶  citec-dashboard(외부) 등 어떤 클라이언트든
```

이 API는 **DB만 읽는다** — `data/raw/incident_reports/*.md` 원본 파일이
없어도(예: raw 파일 없이 이미 ingest만 되어 있는 운영 서버) 정상 동작한다.

## 3. 핵심 개념

### 3.1 CI-TEC 11개 도메인 (`app.frames.citec_taxonomy.CITEC_DOMAINS`)

```
Linux, Windows, VMware, OpenStack, Kubernetes, Middleware,
Network, Storage, Ceph, Database, 성능
```

- 10개는 요청하신 컴포넌트 그대로, **`성능`은 교차축(cross-cutting)**이다 —
  다른 컴포넌트와 별개가 아니라 **함께** 붙는다(예: "DB Hang"은 `Database`+`성능`
  둘 다). 성능 저하가 원인인 장애를 컴포넌트 무관하게 따로 볼 수도 있고,
  특정 컴포넌트의 성능 이슈만 볼 수도 있다.
- **다중 라벨**: 한 장애가 여러 도메인에 동시에 태깅될 수 있다. `group_by=domain`으로
  집계하면 한 건이 N개 도메인 그룹에 각각 카운트된다 — "이 도메인이 걸린
  건수"이지 "이 도메인 단독인 건수"가 아니다.
- **SCP(Samsung Cloud Platform) v1/v2 특례**: `SCP v1`/`SCPv1` → VMware,
  `SCP v2`/`SCPv2` → OpenStack. 버전 표기 없는 바어(bare) "SCP"(코퍼스의
  압도적 다수)는 **둘 중 어디에도 안 붙는다** — v1/v2 구분이 의미 없는 장애가
  섞여 있다는 박재화 확인(2026-09-16)에 따른 설계.
- 실측 커버리지(2026-09-16, 전체 15,333건): Network 58.8%, 성능 6.8%,
  Database 3.8%, Middleware 3.6%, Storage 2.6%, VMware 1.6%, Windows 1.0%,
  Kubernetes 0.9%, Linux 0.5%, OpenStack 0.4%, Ceph 0.1%.

### 3.2 심각도 티어 (`app.frames.citec_taxonomy.classify_severity_tier`)

SWIM `최종등급` → 티어. 박재화 정의(2026-09-16) 그대로:

| tier | 원본 등급 | 의미 |
|---|---|---|
| `major` | 1~3등급 | 중대장애 |
| `minor` | 4등급 | 장애는 있었으나 서비스 영향 미미/없음 |
| `failover_no_impact` | FO등급 | Failover되어 서비스 영향 없었던 장애 |
| `customer_fault` | X등급 | 고객사 귀책 (SDS 책임 아님) |
| `vendor_fault` | N등급 | 벤더 귀책 |
| `unknown` | 그 외/공백/미인식(`LI등급`/`LR등급` 등) | 분류 안 됨 |

실측 분포(전체 15,333건): `failover_no_impact` 7,024 / `minor` 4,258 /
`customer_fault` 2,183 / `unknown` 1,077 / `major` 420 / `vendor_fault` 371.
**`major`(진짜 중대장애)는 전체의 2.7%뿐**이다 — 대시보드에서 `major`만
필터링해 보고 싶다면 `severity_tiers=major`를 쓸 것.

### 3.3 group_by 차원 5가지

| 차원 | 값 출처 | 비고 |
|---|---|---|
| `domain` | `issue_frames.citec_domains` | 다중 라벨 fan-out (위 3.1 참고) |
| `severity_tier` | `issue_frames.severity_tier` | 6개 값 (위 3.2) |
| `dept` | `documents.metadata_['운영부서']` | SWIM 헤더 필드 원문 그대로(정규화 없음) |
| `customer` | `documents.metadata_['고객사']` | SWIM 헤더 필드 원문 그대로 |
| `year` | `documents.metadata_['발생일시(한국)']`의 연도 | 파싱 실패 시 `"(미상)"` |

**최대 3개까지 조합 가능**(`MAX_GROUP_DIMS`) — 4개 이상은 그룹이 급격히
잘게 쪼개져 `min_count` 이하로 떨어지는 경우가 많아 API가 400을 반환한다.
더 세밀하게 보고 싶으면 필터(`domains=`/`severity_tiers=`/`dept_contains=`
등)로 모집단을 좁힌 뒤 3차원 조합을 쓸 것.

## 4. 배치 job — citec_domains/severity_tier 채우기

API가 읽는 `issue_frames.citec_domains`/`severity_tier`는 **미리 계산해서
저장해 둬야 한다** — 요청마다 SWIM 15,333건을 다시 파싱하지 않는다.

```bash
# 컨테이너 안에서 (docker compose exec 패턴, scripts/*.sh 참고)
python -m app.frames.cli --citec-domains --source-type incident_reports [-v] [--force]

# 또는 API 트리거 (배치를 API 서버 프로세스 안에서 동기 실행 — 대량이면 오래 걸릴 수 있음)
curl -X POST http://localhost:8573/v1/frames/extract-citec-domains \
  -H 'Content-Type: application/json' \
  -d '{"source_type": "incident_reports"}'
```

- **idempotent**: `severity_tier IS NOT NULL`을 "이미 처리됨" 기준으로 써서,
  재실행하면 새 문서만 처리한다(`--force`로 전체 재처리).
- **raw 파일 불필요**: `documents.body_md`/`metadata_`만 읽는다.
- **알려진 한계**: 문서가 나중에 업데이트돼도(예: `진행상태: 조치중 →
  종료확정`으로 등급이 뒤늦게 확정) 자동 재처리 안 됨 — `extract_frames()`도
  동일한 구조라 새로 생긴 제약은 아니다. 등급이 자주 바뀌는 운영 환경이면
  주기적으로 `--force` 전체 재처리를 권장한다.
- **실측**(2026-09-16, 스크래치 DB, 실제 15,333건): 전부 성공, 에러 0, 재실행
  시 `processed: 0`으로 정확히 스킵 확인.

운영 서버에 SWIM을 매일 증분 ingest하는 기존 파이프라인이 있다면, 그 뒤에
이 배치를 이어붙이는 크론 래퍼를 만들 수 있다(예: `scripts/confluence_sync.sh`
패턴) — 아직 만들지 않았다, 필요하면 요청할 것.

## 5. REST API 레퍼런스

Base URL: citec-kb API 서버 (`CITEC_KB_BASE_URL`, 기본 개발 `http://localhost:8573`).
인증 불필요 — 이 3개 엔드포인트는 전부 읽기 전용 GET이고, 이 저장소 관례상
(`GET /v1/failure-buckets`와 동일) 읽기 GET은 인증을 요구하지 않는다.

### 5.1 `GET /v1/citec-dashboard/domains`

정적 레전드 — DB 조회 없음, 캐시해서 써도 된다.

**요청**: 파라미터 없음.

**응답**:
```json
{
  "domains": ["Linux", "Windows", "VMware", "OpenStack", "Kubernetes",
              "Middleware", "Network", "Storage", "Ceph", "Database", "성능"],
  "severity_tiers": {
    "major": "1~3등급 — 중대장애",
    "minor": "4등급 — 장애는 있었으나 서비스 영향 미미/없음",
    "failover_no_impact": "FO등급 — Failover되어 서비스 영향 없었던 장애",
    "customer_fault": "X등급 — 고객사 귀책 (SDS 책임 아님)",
    "vendor_fault": "N등급 — 벤더 귀책",
    "unknown": "분류 안 됨 (미인식 등급값 또는 공백)"
  },
  "group_by_dimensions": ["domain", "severity_tier", "dept", "customer", "year"],
  "max_group_by_dimensions": 3
}
```

### 5.2 `GET /v1/citec-dashboard/recurring-patterns`

핵심 쿼리 엔드포인트. 패널 "컴포넌트별 반복 순위"/"컴포넌트×심각도"/
"컴포넌트×부서" 전부 이 엔드포인트 하나로 만든다(그룹 차원만 다르게).

**쿼리 파라미터**:

| 파라미터 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `group_by` | string (CSV) | ✅ | 1~3개, `domain,severity_tier,dept,customer,year` 중 |
| `domains` | string (CSV) | | 필터할 도메인(예: `Network,Storage`), 비우면 전체 |
| `severity_tiers` | string (CSV) | | 필터할 티어(예: `major,minor`), 비우면 전체 |
| `dept_contains` | string | | `운영부서` 부분일치(대소문자 구분, 정확 매칭 아님) |
| `customer_contains` | string | | `고객사` 부분일치 |
| `since_days` | int | | 최근 N일 이내 발생 건만(예: `730`=약 2년). 생략하면 전체 기간 |
| `min_count` | int | | 이 값 미만 그룹 제외 (기본 `3` — "반복"의 최소 기준) |
| `limit` | int | | 반환 그룹 수 상한(건수 내림차순, 기본 `50`, 최대 `500`) |
| `samples_per_group` | int | | 그룹당 대표 사례 수(최신순, 기본 `3`, 0~20) |

**응답**:
```json
{
  "group_by": ["domain", "severity_tier"],
  "filters": { "domains": ["Network"], "severity_tiers": null, "dept_contains": null,
               "customer_contains": null, "since_days": 730, "min_count": 3 },
  "candidate_count": 1046,
  "truncated": false,
  "groups": [
    {
      "group": { "domain": "Network", "severity_tier": "customer_fault" },
      "count": 129,
      "customer_count": 18,
      "samples": [
        { "external_id": "26090861357", "title": "[삼성중공업] 거제사업장-상암DC 인터넷회선 이중화전환",
          "url": "https://devops.sdsdev.co.kr/confluence/...", "occurred_at": "2026-09-08" }
      ]
    }
  ]
}
```

- `candidate_count`: `domains`/`severity_tiers` 필터(DB 인덱스 필터)를 통과한
  전체 문서 수 — `since_days`/`dept_contains`/`customer_contains`/`min_count`
  적용 **이전** 숫자다. "필터를 너무 좁게 걸었나"를 판단하는 기준으로 쓸 것.
- `truncated: true`면 `limit`보다 조건을 만족하는 그룹이 더 있다는 뜻 —
  `limit`을 늘리거나 필터를 좁혀서 재조회할 것.
- `group.domain`이 빈 문자열(`""`)로 나오는 경우는 없다 — `group_by`에
  `domain`이 없을 때만 그 키 자체가 응답에 없다.

**오류**:
- `400` — `group_by`에 잘못된 값이 있거나(`invalid group_by dimension(s): [...]`),
  4개 이상 조합했을 때(`group_by supports at most 3 dimensions, got N`).

**예시**:
```bash
# 최근 2년, 도메인별 반복 순위 (패널 1)
curl -s 'http://localhost:8573/v1/citec-dashboard/recurring-patterns?group_by=domain&since_days=730' | jq .

# 도메인 x 심각도 (패널 2 — 히트맵 재료)
curl -s 'http://localhost:8573/v1/citec-dashboard/recurring-patterns?group_by=domain,severity_tier' | jq .

# 도메인 x 운영부서 (패널 3)
curl -s 'http://localhost:8573/v1/citec-dashboard/recurring-patterns?group_by=domain,dept&min_count=5' | jq .

# Kubernetes만, 중대장애만, 최근 1년
curl -s 'http://localhost:8573/v1/citec-dashboard/recurring-patterns?group_by=dept&domains=Kubernetes&severity_tiers=major&since_days=365' | jq .
```

### 5.3 `GET /v1/citec-dashboard/failure-bucket-coverage`

패널 "failure_bucket 미등록 갭" — CI-TEC 11개 도메인별로 반복 장애 건수 vs
기존 `failure_buckets`(실패 패턴 라이브러리) 등록 현황을 비교한다.

**쿼리 파라미터**: `since_days`(기본 `730`), `min_count`(기본 `3`).

**응답**:
```json
{
  "since_days": 730,
  "min_count": 3,
  "domains": [
    { "domain": "Network", "recurring_incident_count": 405, "fb_domain": "network",
      "failure_bucket_count": 0, "status": "gap" },
    { "domain": "성능", "recurring_incident_count": 148, "fb_domain": null,
      "failure_bucket_count": null, "status": "no_fb_domain_defined" }
  ]
}
```

`status` 값:

| status | 의미 |
|---|---|
| `gap` | 반복 장애가 확인됐는데 매핑된 `fb_domain`에 failure_bucket이 0건 — **정리 필요** |
| `covered` | 매핑된 `fb_domain`에 failure_bucket이 1건 이상 있음 |
| `no_recurring_pattern` | fb_domain은 있지만 이 기간엔 반복 장애가 없음 |
| `no_fb_domain_defined` | 이 CI-TEC 도메인에 대응하는 `fb_domain`이 아직 없음(§7 참고) — **"커버리지 0%"가 아니라 "측정 불가"** |

## 6. MCP 도구 레퍼런스

`mcp-server/server.py`에 3개 도구 추가(citec-kb API를 호출하는 얇은 프록시,
기존 `kb_*` 도구와 동일한 패턴). Claude Code/Desktop 등 MCP 클라이언트에서
바로 쓸 수 있다.

### `kb_citec_domain_catalog()`
파라미터 없음. §5.1을 사람이 읽기 좋은 텍스트로 변환해서 반환. 다른
`kb_citec_*` 도구를 쓰기 전에 값(도메인명/티어명 철자)을 확인하는 용도.

### `kb_citec_recurring_patterns(group_by, domains="", severity_tiers="", dept_contains="", customer_contains="", since_days=0, min_count=3, limit=30)`
§5.2를 감싼다. `since_days=0`은 "전체 기간"(API의 `since_days` 생략과 동일).
예:
```
kb_citec_recurring_patterns(group_by="domain", since_days=730)
kb_citec_recurring_patterns(group_by="domain,dept", domains="Network,Storage", min_count=5)
```

### `kb_citec_failure_bucket_coverage(since_days=730, min_count=3)`
§5.3을 감싼다. `status=gap`인 도메인을 "⚠ 정리 필요"로 표시.

## 7. 알려진 한계 / 설계상 트레이드오프

1. **키워드 기반 태깅은 완벽하지 않다.** `app.frames.citec_taxonomy`는
   정규식 매칭이다 — 본문 어딘가의 단어 하나로 도메인이 붙을 수 있다. 실제로
   "AD서버"라는 부분 문자열만으로 무관한 팩스서버 DNS 수정 건이 Windows로
   잘못 태깅된 사례를 발견해 "Active Directory"/"Windows Server" 전체 구문
   요구로 좁혔다(테스트로 고정, `test_citec_taxonomy.py`) — 비슷한 종류의
   오탐이 다른 도메인에도 남아있을 수 있다. 태깅 결과는 "1차 신호"로 쓸 것.
2. **바어 "SCP" 미분류는 의도적**이다(§3.1) — v1/v2 구분이 필요 없는 장애가
   섞여 있어서, 억지로 VMware나 OpenStack에 붙이지 않는다.
3. **`failure_bucket` 커버리지 비교는 3개 도메인(Network/Windows/Linux)만
   진짜 gap 분석이 된다.** `fb_domain` 어휘(`network`/`cluster`/`windows`,
   `references/failure-bucket-domains.md`)는 진단 플러그인이 소유하는 별도
   체계라 CI-TEC 11개 도메인과 대부분 대응이 없다. 새 `fb_domain`을 추가하려면
   그 문서의 "새 도메인 추가 절차"(PR + `app/taxonomy.py` 매핑)를 밟아야
   한다 — 이 기능이 임의로 매핑을 만들지 않는다.
4. **시간 필터는 Python에서 처리한다**, SQL이 아니라. `발생일시(한국)`가
   `documents.metadata_`(JSONB) 안 자유텍스트라 인덱스가 없다 — 후보군을
   먼저 `citec_domains`/`severity_tier`(둘 다 인덱스 있음)로 좁힌 뒤 Python에서
   날짜/부서/고객사 필터를 적용한다. 현재 규모(15,333건)에서는 충분히
   빠르지만, 코퍼스가 훨씬 커지면 재검토가 필요할 수 있다.
5. **group_by 최대 3차원.** 4차원 이상은 그룹이 급격히 희소해져 유용성이
   떨어진다고 판단해 API 레벨에서 막았다(§3.3).
6. **등급이 나중에 바뀌어도 자동 재태깅 안 됨**(§4) — `--force` 필요.
7. **`major`(중대장애)가 전체의 2.7%뿐**이라 `severity_tiers=major`로 좁히면
   그룹이 `min_count`(기본 3) 밑으로 빠르게 떨어질 수 있다 — 필요하면
   `min_count`를 낮추거나 `since_days`를 늘릴 것.

## 8. citec-dashboard(또는 다른 클라이언트) 연동 가이드

이 API는 citec-kb 프로세스 안에서 도는 일반 REST 엔드포인트다 — 별도 인증
없이 네트워크로 도달 가능하면(`docker-compose.yml`의 `api` 서비스, 기본
포트 `8573`) 어떤 언어/프레임워크에서도 호출 가능하다.

```python
# citec-dashboard(FastAPI) 쪽 예시 — httpx로 citec-kb 호출
import httpx

CITEC_KB_BASE_URL = "http://citec-kb-api:8000"  # 같은 docker network면 서비스명, 아니면 host:8573

async def fetch_recurring_patterns(group_by: str, **params):
    async with httpx.AsyncClient(base_url=CITEC_KB_BASE_URL, timeout=30) as client:
        resp = await client.get("/v1/citec-dashboard/recurring-patterns",
                                 params={"group_by": group_by, **params})
        resp.raise_for_status()
        return resp.json()
```

```javascript
// 브라우저/프론트엔드 쪽 예시 (citec-dashboard가 직접 citec-kb를 프록시 없이 부르는 경우 — CORS 설정 확인 필요)
const res = await fetch(
  `${CITEC_KB_BASE_URL}/v1/citec-dashboard/recurring-patterns?group_by=domain,severity_tier&since_days=730`
);
const data = await res.json();
```

**시각화 아이디어 매핑** (실제 만들 화면은 citec-dashboard 쪽 판단):
- `group_by=domain` → 가로 막대(도메인별 반복 건수 순위)
- `group_by=domain,severity_tier` → 히트맵/누적막대(도메인×심각도)
- `group_by=domain,dept` → 트리맵 또는 그룹 막대(부서별 취약 도메인)
- `group_by=domain,year` → 라인 차트(도메인별 추이)
- `/failure-bucket-coverage` → 상태 배지 리스트("⚠ 정리 필요" 강조)

**주의**: citec-dashboard 프로젝트 자체는 Jira/Confluence 활동 추적용으로
이미 SQLite+FastAPI 구조가 갖춰져 있다(`~/dev/citec-dashboard/README.md`
참고) — 이 API를 호출하는 새 화면/라우트를 그 프로젝트에 추가하는 작업은
**이번 범위에 포함되지 않았다.**

## 9. 운영 배포 체크리스트

- [ ] 마이그레이션 `20260916_0006` 적용 (`issue_frames.citec_domains`/`severity_tier` 컬럼)
- [ ] `python -m app.frames.cli --citec-domains --source-type incident_reports` 최초 실행
  (또는 `POST /v1/frames/extract-citec-domains`)
- [ ] 정기 재실행 방안 결정 — SWIM 증분 ingest 크론이 있다면 그 뒤에 이 배치를
  이어붙일지, 별도 크론으로 뺄지 (§4의 등급-변경 재태깅 한계도 함께 고려)
- [ ] `GET /v1/citec-dashboard/domains`로 배포 확인(DB 조회 없이 즉시 응답해야 함)
- [ ] `GET /v1/citec-dashboard/recurring-patterns?group_by=domain`으로 실데이터
  확인(`candidate_count`가 0이면 배치 job이 아직 안 돈 것)
- [ ] MCP 서버 재시작(새 도구 3개 반영)

이 문서 자체의 배포/크론 등록은 하지 않았다 — 운영 반영은 담당자(박재화) 승인
후 별도 진행.

## 10. 코드 위치

- `apps/api/app/frames/citec_taxonomy.py` — 11개 도메인 태깅 + 심각도 티어 분류 (순수 함수)
- `apps/api/app/frames/job.py` — `extract_citec_domains()` 배치
- `apps/api/app/frames/cli.py` — `--citec-domains` 플래그
- `apps/api/app/routers/frames.py` — `POST /v1/frames/extract-citec-domains`
- `apps/api/app/citec_dashboard/service.py` — 쿼리 로직 (`query_recurring_patterns`/`failure_bucket_coverage`/`domain_catalog`)
- `apps/api/app/routers/citec_dashboard.py` — REST 엔드포인트 3개
- `apps/api/alembic/versions/20260916_0006_issue_frames_citec_domains.py` — 스키마
- `mcp-server/server.py` — MCP 도구 3개 (`kb_citec_domain_catalog`/`kb_citec_recurring_patterns`/`kb_citec_failure_bucket_coverage`)
- 테스트: `apps/api/tests/test_citec_taxonomy.py` (순수 함수, DB 불필요),
  `test_frames_job_citec_domains.py`, `test_citec_dashboard_service.py` (이 둘은 스크래치 DB
  필요 — `CONFLUENCE_SYNC_TEST_DATABASE_URL` 설정, `test_confluence_sync_db.py` docstring 참고)
- 관련 문서: `docs/CONFLUENCE_MAP.md`(무관한 별개 기능), `references/failure-bucket-domains.md`(§7-3)
