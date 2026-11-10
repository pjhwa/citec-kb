"""Regression: the app is masked and no route can sit outside the masked URL space."""

from app.main import app
from app.masking.middleware import IN_SCOPE, MaskingMiddleware

# FastAPI 내장 문서/루트 — 데이터 응답이 아니다.
OUT_OF_SCOPE_OK = {"/", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}


def test_middleware_installed():
    assert any(m.cls is MaskingMiddleware for m in app.user_middleware)


def test_every_route_is_in_masked_url_space():
    stray = sorted(
        {r.path for r in app.routes if hasattr(r, "path")}
        - OUT_OF_SCOPE_OK
        - {p for p in (r.path for r in app.routes if hasattr(r, "path")) if p.startswith(IN_SCOPE)}
    )
    assert not stray, f"routes outside {IN_SCOPE} bypass masking: {stray}"


def test_health_reports_masking_status():
    from app.main import _masking_health

    h = _masking_health()
    assert h["dictionary"] in ("ok", "missing") or h["dictionary"].startswith("error")
