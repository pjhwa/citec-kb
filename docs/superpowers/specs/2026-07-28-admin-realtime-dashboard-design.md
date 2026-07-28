# Admin real-time dashboard

Date: 2026-07-28

## Purpose

`citec-wiki-qa`(참고 프로젝트)의 `/admin` 페이지처럼, 운영자가 citec-kb의 인제스트/임베딩
진행률, 작업 큐, 시스템 리소스, 쿼리 사용량을 한 화면에서 주기적으로 자동 갱신되는 형태로
확인할 수 있게 한다. 현재 `apps/web/public/admin.html`은 정적 상태만 보여주고
자동 새로고침이 없다.

## Non-goals

- 컨테이너별(Docker) CPU/메모리 — API 프로세스 기준 리소스만 노출 (docker socket 접근 없음).
- 실시간 애플리케이션 로그 뷰어 — citec-kb는 컨테이너 stdout 로깅 구조라 파일 tail 방식이
  맞지 않음. `docker compose logs`로 충분.
- WebSocket/SSE push — 주기적 폴링으로 충분하다고 확인됨.
- eval 점수 추이 차트 — 이번 스코프에서 제외 (별도 요청 시 후속 작업).

## Backend

### New endpoint: `GET /v1/ops/dashboard`

`apps/api/app/routers/ops.py`에 추가. `require_roles("admin")`로 보호한다 (쿼리 텍스트를
포함해 `/v1/ops/status`보다 민감한 정보를 노출하므로).

Response shape:

```json
{
  "generated_at": "2026-07-28T12:00:00Z",
  "ingest_progress": {
    "support_history": {
      "raw_files": 2280,
      "documents": 2280,
      "chunks": 15230,
      "chunks_active": 15230,
      "embeddings": 15230,
      "embed_pct": 100,
      "last_job": {"id": "...", "mode": "incremental", "status": "success",
                    "started_at": "...", "finished_at": "...", "error": null}
    },
    "...": "..."
  },
  "jobs": {
    "queue_length": 0,
    "total": 12,
    "items": ["... list_jobs() 결과 재사용 ..."],
    "worker": {"ok": true, "heartbeat": 1234567890, "age_sec": 3}
  },
  "resources": {
    "process_rss_mb": 210.4,
    "load_avg": [0.12, 0.20, 0.18],
    "disk": {"path": "/data/raw", "total_gb": 100.0, "used_gb": 42.1, "pct": 42}
  },
  "queries": {
    "count_1h": 5,
    "count_24h": 84,
    "avg_latency_ms_24h": 812,
    "recent": [
      {"id": "...", "query": "...(최대 120자)", "mode": "rag", "latency_ms": 640,
        "created_at": "..."}
    ]
  }
}
```

구현 세부:

- **ingest_progress**: `data/raw_manifest.json`을 읽어 소스별 `raw_files` 총계를 얻고,
  `Document`/`Chunk`/`Embedding`을 `source_type`으로 그룹핑해 집계. `IngestJob`은
  `source_id`로 조인되므로, 소스별 최신 1건만 `ORDER BY started_at DESC LIMIT 1`로 조회.
  `raw_manifest.json`이 없으면 `raw_files: null`로 두고 `embed_pct`만 표기.
- **jobs**: `app.jobs.queue.list_jobs(limit=20)`을 그대로 호출 (신규 로직 없음).
- **resources**: `shutil.disk_usage(settings.raw_dir)`, `resource.getrusage(RUSAGE_SELF).ru_maxrss`
  (KB→MB 변환, Linux 기준), `os.getloadavg()`. 플랫폼에서 미지원 시 (`AttributeError`/`OSError`)
  해당 필드만 `null` 처리하고 나머지는 정상 응답.
- **queries**: `QueryLog`에서 `created_at >= now - 1h/24h` 카운트, 24h `latency_ms` 평균,
  최근 10건 (query 텍스트는 120자로 자름).
- 각 섹션은 독립적으로 try/except 처리 — 한 섹션이 실패해도 (예: raw_manifest.json 없음)
  나머지는 정상 반환하고 실패 섹션에 `{"error": "..."}` 표기.

기존 `/v1/ops/status`, `/v1/jobs*`, `/v1/ingest/stats` 엔드포인트는 변경하지 않는다.

## Frontend

`apps/web/public/admin.html`을 확장한다 (기존 Session/Platform/Quick-links 카드 유지,
기존 시각 스타일 시스템 그대로 사용 — 참고 프로젝트의 다크 JetBrains-Mono 테마는 그대로
가져오지 않는다).

추가 구성:

1. **자동 새로고침 컨트롤**: "자동 갱신 (10s)" 토글 체크박스 + 수동 새로고침 버튼.
   토글 on 시 `setInterval`로 10초마다 `/v1/ops/dashboard` 재조회. 관리자 권한 없을 시
   403 응답을 감지해 "관리자 권한 필요" 안내로 대체.
2. **리소스 카드**: RSS(MB), load average, disk 사용률 게이지(%) — Platform 카드 옆에 추가.
3. **인제스트/임베딩 진행 테이블**: 소스별 raw_files/documents/chunks/embeddings, 임베딩
   완료율 progress bar, 최신 ingest job 상태 배지.
4. **작업 큐 패널**: queue_length, worker heartbeat 배지, 최근 job 리스트 (job card,
   상태별 색상).
5. **쿼리 사용량 패널**: 1h/24h 카운트 KPI, 평균 지연시간, 최근 쿼리 10건 테이블
   (시각/모드/지연/쿼리 텍스트).

`js/auth.js`의 `CitecAuth.apiFetch`를 재사용해 Authorization 헤더를 자동으로 붙인다
(기존 admin.html이 이미 이 패턴을 사용 중).

## Testing

- `apps/api/tests`에 `/v1/ops/dashboard` 테스트 추가: 200 응답 shape 검증, admin role
  없을 때 401/403 (AUTH_MODE enforced 케이스), raw_manifest.json 없을 때도 500이 아니라
  정상 응답하는지 확인.
- 수동으로 `docker compose up`(또는 로컬 uvicorn) 후 브라우저에서 admin.html 로드해
  자동 갱신 동작 확인.
