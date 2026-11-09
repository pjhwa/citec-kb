# PII 마스킹 운영 런북

## 가시성 (롤 × 분류)
스펙(`docs/superpowers/specs/2026-11-09-pii-masking-design.md`)의 가시성 표를 따른다. 분류는 `pii`(개인정보), `net`(IP), `biz`(고객사명/호스트명).
`senior`/`admin`만 원본 보기 토글로 `pii`/`net` 원본을 볼 수 있고, 열람은 동기 감사 로그가 성공해야 나간다.
`AUTH_MODE=off`이면 익명 요청은 author 수준으로 취급하며 원본 열람은 불가하다.

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
1. `docker compose exec api alembic upgrade head`
2. `docker compose up -d --build api web`
3. MCP 컨테이너는 `server.py` 변경을 반영하려면 재빌드가 필요하다: `docker compose up -d --build mcp`

## 롤백
미들웨어 등록 줄 한 줄을 되돌리고 api를 재빌드한다. 감사 테이블(`unmask_audit_log`)은 남겨 둔다.

## 알려진 한계
- 사번은 `사번` 라벨/열 문맥에서만 탐지한다.
- 이름은 개인정보가 걸린 줄에서만 탐지한다.
- 지번주소 일부는 누락될 수 있다.
- 임베딩 원문과 Confluence 직접 조회는 범위 밖이다.
- SSE(text/event-stream) 응답(chat 등)과 JSON이 아닌 응답(text/xml 등)은 현재 마스킹 대상이 아니다.
