"""A2 — entity_share 출력: matched/total(분모) 라벨, kb_query 경로에서도 분모를 건수로 오해하지 않게."""

import asyncio

import httpx

import server

_RES = {
    "entity": "SCP", "matched": 141, "total": 289, "share": 0.4879, "source_type": "support_history",
    "date_from": "2026-01-01", "date_to": "2026-10-28", "range_label": "2026년(올해)",
    "method": "title_entity_scan", "definition": "matched = 같은 기간… (정의)",
}


def _mock(monkeypatch, payload):
    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))),
    )


def test_kb_entity_share_labels_numerator_and_denominator(monkeypatch):
    _mock(monkeypatch, _RES)
    out = asyncio.run(server.kb_entity_share(entity="SCP", relative="올해"))
    assert "matched(엔티티 포함 건수)=141" in out
    assert "total(분모: 같은 기간·소스 전체)=289" in out
    assert "share=0.4879" in out and "정의:" in out


def test_kb_query_entity_share_mode_does_not_print_bare_total():
    out = server._format_query_response({
        "intent": "analytics", "params": {"mode": "entity_share", "entity": "SCP"},
        "result": _RES, "range_label": "2026년(올해)",
    })
    assert "matched(엔티티 포함 건수)=141" in out
    assert "group_by=None" not in out  # 예전: 'group_by=None total=289'만 나와 전체 건수로 읽힘


def test_kb_query_normal_aggregate_unchanged():
    out = server._format_query_response({
        "intent": "analytics", "params": {},
        "result": {"group_by": "year", "total": 5, "method": "metadata_aggregate",
                   "buckets": [{"key": "2026", "count": 5, "share": 1.0}]},
    })
    assert "group_by=year total=5" in out and "- 2026: 5건" in out
