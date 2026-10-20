"""kb_recent_changes — 요청 파라미터 전달·출력 포맷·오류 처리 (HTTP 모의, DB 불필요)."""

import asyncio

import httpx

import server


def _mock(monkeypatch, handler):
    monkeypatch.setattr(
        server,
        "_client",
        lambda timeout=30.0: httpx.AsyncClient(base_url="http://t", transport=httpx.MockTransport(handler)),
    )


def _payload(**over):
    base = {
        "total": 2, "limit": 30, "offset": 0, "change_type": "modified",
        "range": {"date_from": "2026-10-14", "date_to": "2026-10-20", "label": "최근 7일"},
        "missing_modified_at": 0,
        "items": [
            {"title": "Redis 가이드", "source_type": "confluence_docs", "space_key": "LOOKIN",
             "path": "오픈스택", "page_id": "111", "url": "https://c/111", "source_version": 7,
             "source_modified_at": "2026-10-20T09:00:00.000+09:00",
             "kb_created_at": "2026-09-01T00:00:00+00:00", "kb_updated_at": None, "change": "modified"},
            {"title": "폴더없음", "source_type": "tech_repo", "space_key": "TechRepo", "path": None,
             "page_id": "222", "url": None, "source_version": None,
             "source_modified_at": None, "kb_created_at": None, "kb_updated_at": None, "change": "added"},
        ],
    }
    base.update(over)
    return base


def test_params_forwarded_and_blank_values_dropped(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=_payload())

    _mock(monkeypatch, handler)
    asyncio.run(server.kb_recent_changes(relative="최근 7일", space_key=" DevOps001 ", change_type="all", limit=999))
    assert seen["path"] == "/v1/documents/recent-changes"
    p = seen["params"]
    assert p["relative"] == "최근 7일" and p["space_key"] == "DevOps001"
    assert p["change_type"] == "all" and p["limit"] == "200"  # clamped
    assert "date_from" not in p and "title_contains" not in p and "source_types" not in p


def test_output_format_and_notes(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_payload(missing_modified_at=5, change_type="all", total=2)))
    out = asyncio.run(server.kb_recent_changes(change_type="all"))
    assert out.splitlines()[0].startswith("recent_changes total=2 change_type=all range=최근 7일")
    assert "수정 시각 정보가 없는 문서 5건" in out
    assert "KB가 처음 수집한 시각" in out  # added 주의 문구
    assert "- [modified] Redis 가이드 (space=LOOKIN, type=confluence_docs, page_id=111" in out
    assert "path: 오픈스택" in out and "url: https://c/111" in out
    assert "v=7" in out and "- [added] 폴더없음" in out


def test_no_added_note_in_modified_mode_and_empty_result(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json=_payload(items=[], total=0)))
    out = asyncio.run(server.kb_recent_changes())
    assert "KB가 처음 수집한 시각" not in out
    assert "조건에 맞는 변경이 없습니다" in out


def test_400_returns_invalid_request_with_detail(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(400, json={"detail": "unrecognized relative range: 쓸데없는"}))
    out = asyncio.run(server.kb_recent_changes(relative="쓸데없는"))
    assert out.startswith("status: invalid_request")
    assert "unrecognized relative range" in out


def test_backend_down_returns_error_string_not_exception(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    _mock(monkeypatch, handler)
    out = asyncio.run(server.kb_recent_changes())
    assert out.startswith("status: backend_error")


def test_tool_is_registered_and_documented_in_help():
    assert asyncio.run(server.kb_tools_help()).count("kb_recent_changes") >= 1
    names = [t.name for t in asyncio.run(server.mcp.list_tools())]
    assert "kb_recent_changes" in names
