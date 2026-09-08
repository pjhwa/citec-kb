# citec-kb: 문서 업로드용 MCP 도구 추가 (`kb_upload_document`)

## 배경

MY-OS 쪽 SWIM 증분 수집 파이프라인(`swim_collect_runner.py`)이 매 실행마다 신규/변경
SWIM 장애 몇 건을 로컬 SQLite에 반영한 뒤, 그 소수 건을 citec-kb에도 곧바로 반영하고
싶다. 지금까지(Phase 1) 15,333건 최초 대량 적재는 압축파일을 물리적으로 운영서버에
옮겨 `data/raw/`에 두고 `python -m app.ingest.cli`를 돌리는 방식이었는데, 이건 매번
소수 건(보통 0~수건) 반영하기엔 너무 무겁다.

이미 `apps/api/app/routers/external_compat.py`에 `POST /api/upload`(wiki-qa 호환용,
파일 1개 + `source_type`을 받아 `raw_dir/<source_type>/`에 써넣고 백그라운드로 ingest
큐에 넣는 기존 엔드포인트)가 있다 — **이걸 재구현하지 말고 그대로 감싸는 MCP 도구만
추가**한다.

## 목표

`mcp-server/server.py`에 아래 도구 하나를 추가한다:

```python
@mcp.tool()
async def kb_upload_document(
    filename: str,
    content: str,
    source_type: str = "incident_reports",
) -> str:
    """문서 1건을 업로드해 즉시 ingest 큐에 넣는다 (기존 POST /api/upload 래핑).

    filename: 확장자 포함 파일명 (예: 26090761356.md)
    content: 파일 전체 텍스트(마크다운 원문)
    source_type: 기본 incident_reports — 다른 source_type도 넘길 수 있지만
                 이번 도구의 주 용도는 SWIM 장애보고서 증분 반영이다.
    """
    if not filename.strip() or not content.strip():
        return "오류: filename/content가 비어 있습니다."
    try:
        async with _client(timeout=60.0) as client:
            files = {"file": (filename, content.encode("utf-8"), "text/markdown")}
            data = {"source_type": source_type}
            resp = await client.post("/api/upload", files=files, data=data)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return (
        f"업로드 큐 등록됨: job_id={body.get('job_id')} "
        f"filename={body.get('filename')} status={body.get('status')}"
    )
```

`_client`/`_err`는 이 파일에 이미 있는 기존 헬퍼(다른 도구들, 예: `kb_similar_incident`의
`async with _client(timeout=90.0) as client:` 패턴)를 그대로 재사용한다 — 새로 만들지
말 것.

## 구현 시 확인할 것

1. `POST /api/upload`는 `multipart/form-data`(`UploadFile` + `Form`)를 받는다.
   `httpx.AsyncClient.post(..., files=..., data=...)`로 멀티파트 인코딩이 되는지
   확인 — 기존 `_client()` 헬퍼가 `base_url`만 잡아주는 얇은 래퍼라면 문제없이 될
   것이다.
2. 응답은 즉시 `{"job_id":..., "filename":..., "status": "queued"}`이고 **ingest 자체는
   백그라운드**다(`background.add_task`) — 이 도구는 실제 적재 완료를 기다리지 않고
   큐 등록 확인까지만 한다. 즉시 검증하고 싶으면 몇 초 뒤 `kb_ticket(external_id=...,
   source_type="incident_reports")`로 별도 확인해야 한다(이 도구 자체에 폴링 로직을
   넣지 않는다 — 불필요한 복잡도).
3. `source_type` 값 검증(화이트리스트 등)을 추가할지는 기존 `/api/upload`가 이미
   `source_type: str = Form(default="support_history")`로 자유 문자열을 받고 있으니,
   이 MCP 도구도 그대로 통과시킨다(추가 검증 불필요 — 이미 있는 경계를 그대로 신뢰).
4. **쓰기 권한 있는 도구다.** README/`docs/MCP.md`에 이 도구가 `kb_register_failure_bucket`
   과 같은 급의 "쓰기" 도구임을 한 줄 추가해 문서화할 것 — 사용자가 도구 목록만 보고도
   구분 가능해야 한다.

## 테스트

`apps/api`에 이미 MCP 도구용 단위테스트가 없다면(도구 자체는 API를 얇게 감싸는 게
전부라 별도 유닛테스트 없이 통합 확인으로 충분할 수 있음), 최소한 로컬 docker-compose
환경에서 실제 호출 1건으로 확인:
```
kb_upload_document(filename="test-upload-1.md", content="[test-upload-1] 테스트 문서\n■ 장애상황: 테스트", source_type="incident_reports")
```
→ 몇 초 후 `kb_ticket(external_id="test-upload-1", source_type="incident_reports")`로
조회되는지 확인. **테스트 후 이 더미 문서는 반드시 삭제할 것**:
```sql
DELETE FROM documents WHERE source_type='incident_reports' AND external_id='test-upload-1';
```

## 완료 후 보고 형식

1. 변경 파일(`mcp-server/server.py`, 문서화 갱신분) diff
2. 위 테스트 호출 결과(업로드 → 조회 확인 → 더미 삭제까지)
3. 배포 완료 여부(운영 MCP 서버 컨테이너 재시작 포함 — `docker compose restart mcp` 또는
   해당 서비스명)

