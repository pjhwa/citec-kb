"""F01(2026-10-13 최종 핸드오프) — 요청 검증 오류(4xx)를 "연결 장애"로
뭉개던 버그의 회귀 테스트. 실제 재현: documents_offset=-1 입력에
HTTP 422가 "citec-kb API에 연결할 수 없습니다"로 나왔다(comparison/raw/
invalid_offset.json). kb_graph_explore는 전체 구조화 계약(CallToolResult +
structuredContent)을, 그 외 ~30개 도구는 _err()을 통해 최소한의 status 구분만
공유한다."""

from __future__ import annotations

import asyncio

import httpx
from mcp.types import CallToolResult

import server


def _run_with_status(monkeypatch, status_code: int, body: dict | None = None, **kwargs):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body or {"detail": "bad"})

    monkeypatch.setattr(
        server,
        "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(handler)
        ),
    )
    return asyncio.run(server.kb_graph_explore("component", "NetApp", **kwargs))


def test_invalid_offset_returns_structured_invalid_request_not_connection_error(monkeypatch):
    """실제 재현 입력(documents_offset=-1) → 422. 과거엔 isError=False +
    "연결할 수 없습니다" 문장뿐이었다. 지금은 CallToolResult로 status/
    error_code를 문자열 정규식 없이 판별할 수 있어야 한다."""
    out = _run_with_status(
        monkeypatch, 422,
        body={"detail": [{"loc": ["body", "documents_offset"], "msg": "입력이 유효하지 않습니다"}]},
        documents_offset=-1,
    )
    assert isinstance(out, CallToolResult)
    assert out.isError is True
    assert out.structuredContent["status"] == "invalid_request"
    assert out.structuredContent["error_code"] == "validation_error"
    assert out.structuredContent["retryable"] is False
    assert out.structuredContent["http_status"] == 422
    assert out.structuredContent["field_errors"] == [
        {"loc": ["body", "documents_offset"], "msg": "입력이 유효하지 않습니다"}
    ]
    # "result" 필드는 기존(-> str) outputSchema를 캐시해둔 클라이언트와의
    # 하위호환을 위해 그대로 유지한다.
    assert out.structuredContent["result"] == out.content[0].text
    text = out.content[0].text
    assert "status: invalid_request" in text
    # F01.6 — 내부 endpoint URL이 사람이 읽는 텍스트에 노출되지 않아야 한다.
    assert "http://" not in text and "api:8000" not in text
    assert "연결" not in text  # "연결할 수 없습니다" 오분류 재발 방지


def test_server_error_is_backend_error_and_retryable(monkeypatch):
    out = _run_with_status(monkeypatch, 500, body={"detail": "internal"})
    assert isinstance(out, CallToolResult)
    assert out.isError is True
    assert out.structuredContent["status"] == "backend_error"
    assert out.structuredContent["error_code"] == "server_error"
    assert out.structuredContent["retryable"] is True
    assert out.structuredContent["http_status"] == 500


def test_real_connection_failure_is_distinguishable_from_validation_error(monkeypatch):
    """진짜 연결 장애(네트워크 자체가 끊김)는 여전히 backend_error/
    connection_error로 분류돼야 한다 — 이번 수정이 "모든 오류를 invalid_request
    로 바꿔버리는" 반대 방향 회귀를 만들지 않았는지 확인."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(handler)
        ),
    )
    out = asyncio.run(server.kb_graph_explore("component", "NetApp"))
    assert isinstance(out, CallToolResult)
    assert out.isError is True
    assert out.structuredContent["status"] == "backend_error"
    assert out.structuredContent["error_code"] == "connection_error"
    assert out.structuredContent["retryable"] is True


def test_classify_http_error_distinguishes_validation_from_connection():
    """_classify_http_error — 다른 ~30개 도구가 공유하는 범용 분류기. 4xx와
    진짜 연결 실패를 구분해야 한다(단위 테스트, HTTP mock 불필요)."""
    resp_422 = httpx.Response(422, json={"detail": "bad input"}, request=httpx.Request("POST", "http://api:8000/v1/graph/explore"))
    err_422 = httpx.HTTPStatusError("422", request=resp_422.request, response=resp_422)
    info = server._classify_http_error(err_422)
    assert info["status"] == "invalid_request"
    assert info["error_code"] == "validation_error"
    assert info["retryable"] is False

    resp_500 = httpx.Response(500, json={"detail": "boom"}, request=httpx.Request("GET", "http://api:8000/v1/health"))
    err_500 = httpx.HTTPStatusError("500", request=resp_500.request, response=resp_500)
    info_500 = server._classify_http_error(err_500)
    assert info_500["status"] == "backend_error"
    assert info_500["error_code"] == "server_error"
    assert info_500["retryable"] is True

    connect_err = httpx.ConnectError("boom")
    info_conn = server._classify_http_error(connect_err)
    assert info_conn["status"] == "backend_error"
    assert info_conn["error_code"] == "connection_error"


def test_err_no_longer_calls_validation_errors_connection_failures():
    """F01 — 기존 ~30개 도구가 공유하는 _err()도 4xx를 더 이상 "연결 장애"
    문구로 뭉개지 않는다(구조화 계약은 없지만 최소한 status 구분은 됨)."""
    resp_422 = httpx.Response(422, json={"detail": "bad"}, request=httpx.Request("POST", "http://api:8000/v1/search"))
    err_422 = httpx.HTTPStatusError("422", request=resp_422.request, response=resp_422)
    text = server._err(err_422)
    assert text.startswith("status: invalid_request")
    assert "연결할 수 없습니다" not in text
    assert "http://" not in text and "api:8000" not in text

    connect_err = httpx.ConnectError("boom")
    text_conn = server._err(connect_err)
    assert text_conn.startswith("status: backend_error")
