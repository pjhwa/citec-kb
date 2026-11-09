"""MaskingMiddleware on non-JSON bodies: text/xml, +json, unknown types (fail closed), empty/redirect."""

from __future__ import annotations

import gzip
import json
import logging
import xml.etree.ElementTree as ET

import pytest
from fastapi.responses import RedirectResponse, Response
from fastapi.testclient import TestClient

from test_masking_middleware import BODY, harness

PII = ("031-123-4567", "10.0.0.1")


@pytest.fixture
def client(monkeypatch):
    app, audit, state = harness(monkeypatch)

    @app.get("/v1/xml")
    def xml():
        return Response(f'<?xml version="1.0"?><mxfile><cell value="{BODY}"/><t>{BODY}</t></mxfile>',
                        media_type="text/xml")

    @app.get("/v1/plain")
    def plain():
        return Response(BODY, media_type="text/plain; charset=utf-8")

    @app.get("/v1/problem")
    def problem():
        return Response(json.dumps({"detail": BODY}), media_type="application/problem+json", status_code=422)

    @app.get("/v1/upper-json")
    def upper_json():
        return Response(json.dumps({"detail": BODY}), media_type="Application/JSON; Charset=UTF-8")

    @app.get("/v1/bin")
    def binary():
        return Response(BODY.encode(), media_type="application/octet-stream")

    @app.get("/v1/notype")
    def notype():
        return Response(BODY.encode())

    @app.get("/v1/gz")
    def gz():
        return Response(gzip.compress(json.dumps({"x": BODY}).encode()), media_type="application/json",
                        headers={"Content-Encoding": "gzip"})

    @app.get("/v1/png")
    def png():
        return Response(b"\x89PNG" + BODY.encode(), media_type="image/png")

    @app.get("/v1/empty")
    def empty():
        return Response(b"", media_type="application/octet-stream")

    @app.get("/v1/nocontent", status_code=204)
    def nocontent():
        return Response(status_code=204)

    @app.get("/v1/redirect")
    def redirect():
        return RedirectResponse("/v1/plain", status_code=302)

    return TestClient(app), audit, state


def _clean(r):
    for v in PII:
        assert v not in r.text


def test_xml_masked_and_still_well_formed(client):
    c, _, _ = client
    r = c.get("/v1/xml", headers={"X-API-Key": "viewer"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/xml")
    _clean(r)
    root = ET.fromstring(r.content)
    assert root.find("cell").get("value") == "call [PHONE-1] host [IP-1] end"
    assert root.find("t").text == "call [PHONE-1] host [IP-1] end"
    assert r.headers["x-citec-masking"] == "net=2,pii=2"
    assert r.headers["content-length"] == str(len(r.content))


def test_text_plain_masked(client):
    c, _, _ = client
    r = c.get("/v1/plain", headers={"X-API-Key": "author"})
    assert r.text == "call [PHONE-1] host [IP-1] end"


def test_text_original_for_senior_unmask_is_identical_bytes(client):
    c, audit, _ = client
    orig = c.get("/v1/xml", headers={"X-API-Key": "admin", "X-Citec-Unmask": "1"})
    assert orig.content == f'<?xml version="1.0"?><mxfile><cell value="{BODY}"/><t>{BODY}</t></mxfile>'.encode()
    assert [a["outcome"] for a in audit] == ["granted"]
    assert orig.headers.get("x-citec-unmask") == "granted"


def test_problem_json_and_case_insensitive_json_masked(client):
    c, _, _ = client
    r = c.get("/v1/problem", headers={"X-API-Key": "viewer"})
    assert r.status_code == 422 and r.json()["detail"] == "call [PHONE-1] host [IP-1] end"
    r = c.get("/v1/upper-json", headers={"X-API-Key": "viewer"})
    assert r.json()["detail"] == "call [PHONE-1] host [IP-1] end"


@pytest.mark.parametrize("path", ["/v1/bin", "/v1/notype", "/v1/gz"])
def test_unknown_or_encoded_body_fails_closed(client, path, caplog):
    c, _, _ = client
    with caplog.at_level(logging.WARNING, logger="citec.masking"):
        r = c.get(path, headers={"X-API-Key": "admin", "X-Citec-Unmask": "1"})
    assert r.status_code == 500 and r.json() == {"detail": "unmaskable content type"}
    _clean(r)
    assert any(path in rec.getMessage() for rec in caplog.records)
    assert not any("031-123-4567" in rec.getMessage() for rec in caplog.records)


def test_image_passthrough(client):
    c, _, _ = client
    r = c.get("/v1/png", headers={"X-API-Key": "viewer"})
    assert r.status_code == 200 and r.content == b"\x89PNG" + BODY.encode()


def test_empty_204_redirect_pass_through(client):
    c, _, _ = client
    r = c.get("/v1/empty", headers={"X-API-Key": "viewer"})
    assert r.status_code == 200 and r.content == b""
    assert c.get("/v1/nocontent").status_code == 204
    r = c.get("/v1/redirect", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/v1/plain"
    r = c.get("/v1/redirect", headers={"X-API-Key": "viewer"})
    assert r.status_code == 200 and r.text == "call [PHONE-1] host [IP-1] end"
