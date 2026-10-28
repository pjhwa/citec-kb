"""A5 — kb_ticket: 해석 안내·did_you_mean·부재 문구 유지."""

import asyncio

import httpx

import server


def _mock(monkeypatch, handler):
    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(base_url="http://t", transport=httpx.MockTransport(handler)),
    )


def test_normalized_note(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={
        "title": "T", "external_id": "CITECTS-2502", "normalized_from": "citects-2502", "body_md": "본문"}))
    out = asyncio.run(server.kb_ticket("citects-2502"))
    assert out.startswith("(입력 'citects-2502' → CITECTS-2502 로 해석)")
    assert "# T" in out


def test_exact_has_no_note(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"title": "T", "external_id": "CITECTS-1", "body_md": "b"}))
    assert asyncio.run(server.kb_ticket("CITECTS-1")).startswith("# T")


def test_did_you_mean_is_suggestion_only(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(404, json={"detail": {"message": "ticket not found", "did_you_mean": "CITECTS-2502"}}))
    out = asyncio.run(server.kb_ticket("2502"))
    assert "찾을 수 없습니다" in out  # 스킬이 가짜 번호 판별에 쓰는 문구 유지
    assert "혹시 CITECTS-2502 인가요?" in out


def test_plain_404_unchanged(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(404, json={"detail": "ticket not found"}))
    out = asyncio.run(server.kb_ticket("CITECTS-99999"))
    assert out == "오류: 티켓을 찾을 수 없습니다: CITECTS-99999."
