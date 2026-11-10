# PII 마스킹 운영 런북

## 가시성 (롤 × 분류)
스펙(`docs/superpowers/specs/2026-11-09-pii-masking-design.md`)의 가시성 표를 따른다. 분류는 `pii`(개인정보), `net`(IP), `biz`(고객사명/호스트명).
`senior`/`admin`만 원본 보기 토글로 `pii`/`net` 원본을 볼 수 있고, 열람은 동기 감사 로그가 성공해야 나간다.
`AUTH_MODE=off`이면 익명 요청은 author 수준으로 취급하며 원본 열람은 불가하다(웹 UI도 이때 원본 보기 토글을 숨긴다).

## 사전(dictionary)
- `config/masking_dictionary.json`에 고객사명·호스트명 패턴·allowlist를 적는다. 작성법은 `config/masking_dictionary.json.example` 참조.
- 파일 수정은 즉시 반영된다(재시작 불필요).
- 상태 확인: `/v1/health`의 `checks.masking`.

## 원본 열람 조회
```sql
SELECT created_at, sub, roles, path, counts, outcome FROM unmask_audit_log ORDER BY id DESC LIMIT 50;
```

## 실데이터 점검
`scripts/masking_probe.py`는 문서 본문이나 매칭 값을 출력하지 않고 건수만 낸다.
- DB 모드: `python3 scripts/masking_probe.py --ancestor 224797007` (`DATABASE_URL` 필요)
- DB 없이: `python3 scripts/masking_probe.py --stdin-jsonl --residual < docs.jsonl` (줄마다 `{"external_id","title","body"}`)
- `--summary-only`는 문서별 줄을 생략, `--residual`은 마스킹된 본문을 원본 패턴으로 재검사한다(0이어야 하며 0 초과는 마스킹 버그).

## 배포
1. 이미지를 먼저 재빌드한다(실행 중인 컨테이너에는 새 마이그레이션 파일이 없어서 순서가 바뀌면 `alembic upgrade head`가 새 리비전을 찾지 못한다): `docker compose up -d --build api mcp`
2. 마이그레이션을 적용한다: `docker compose exec api alembic upgrade head` (`alembic current`가 `20261109_0024 (head)`인지 확인)
3. `web`은 `apps/web/public`을 마운트하므로 재빌드가 필요 없다. 새 파일이 서빙되는지만 확인한다.
4. `/v1/health`의 `checks.masking`이 `ok: true`인지 확인한다. `dictionary: missing`이면 `biz`(고객사명·호스트명)만 꺼진 상태다.

## 롤백
미들웨어 등록 줄 한 줄을 되돌리고 api를 재빌드한다. 감사 테이블(`unmask_audit_log`)은 남겨 둔다.

## 알려진 한계
- 사번은 `사번` 라벨/열 문맥에서만 탐지한다.
- 이메일(`user@domain.tld`)은 pii로 `[EMAIL-n]`으로 가린다. 같은 줄의 이름 후보 마스킹을 유발하지 않는다. 난독화 표기(`a[at]b.com`)는 탐지하지 않는다.
- 이름은 개인정보가 걸린 줄에서만 탐지한다.
- 지번주소 일부는 누락될 수 있다.
- 임베딩 원문과 Confluence 직접 조회는 범위 밖이다.
- SSE(`/api/query`, `/v1/chat` stream=true, `/api/ingest-status`)는 버퍼링한 뒤 마스킹하므로 마스킹 대상 사용자(원본 허용이 아닌 모든 요청)는 실시간 토큰 표시를 잃고 답변이 끝난 뒤 한 번에 받는다. `/api/ingest-status`는 작업이 끝날 때까지(최대 약 2분) 아무 바이트도 오지 않는다.
- `image/`·`font/` 외의 바이너리나 콘텐츠 타입이 없는 응답, 압축(`content-encoding`) 응답은 500(`unmaskable content type`)으로 막힌다. 새 라우트가 이런 응답을 내면 미들웨어 분류를 먼저 고쳐야 한다(로그 `citec.masking`의 `unmaskable response blocked`).
- `/v1/ops/`는 의도적으로 마스킹하지 않는다. MCP 질의 원문과 클라이언트 IP가 admin에게 그대로 보이며, `AUTH_MODE=off`에서는 누구에게나 보인다.
- 사전 소속 여부 노출: 질의를 응답에 되돌려 주는 엔드포인트는 사전에 있는 고객사명을 `[CUST-n]`으로 바꿔 돌려주므로, 질의를 바꿔 보며 어떤 이름이 사전에 있는지 알아낼 수 있다(viewer 대상).
- 거부(denied) 감사 행은 노출될 값이 있을 때만, 같은 사용자·경로당 60초에 한 번만 남는다(프로세스 단위). 감사 `client_ip`의 `xri=` 뒤 값은 위조 가능한 헤더 값이다.
