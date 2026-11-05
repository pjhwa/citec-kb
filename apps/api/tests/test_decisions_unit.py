import unicodedata

import pytest

from app.decisions import rules
from app.decisions.service import resolve_actor
from app.auth.principal import ANON_OPEN, Principal

OPTS = [{"key": "a", "label": "2026.11.30"}, {"key": "b", "label": "연장"}]


def test_lint_adds_reserved_and_rejects_bad_options():
    out, warn = rules.lint_options("PIXEL 제공 종료일은?", OPTS)
    assert [o["key"] for o in out] == ["a", "b", "NEI", "OTHER"] and warn == []
    for bad in (
        [{"key": "NEI", "label": "x"}, OPTS[0]],
        [OPTS[0], {"key": "a", "label": "다른"}],
        [OPTS[0], {"key": "c", "label": " 2026.11.30 "}],
        [OPTS[0]],
    ):
        with pytest.raises(ValueError):
            rules.lint_options("q?", bad)


def test_compound_question_warns_not_blocks():
    _, warn = rules.lint_options("종료일 및 담당자는?", OPTS)
    assert warn


def test_quote_matching_nfc_and_whitespace():
    body = "PIXEL (엑셀기반 PISA진단)_2026.11.30   까지\n제공"
    nfd = unicodedata.normalize("NFD", "엑셀기반 PISA진단)_2026.11.30 까지 제공")
    assert nfd != unicodedata.normalize("NFC", nfd)
    assert rules.quote_in_body(nfd, body)
    assert not rules.quote_in_body("2026.12.31", body)
    assert not rules.quote_in_body("   ", body)


def test_conflict_rules():
    ev = lambda k, st, **kw: {"option_key": k, "stance": st, **kw}  # noqa: E731
    assert not rules.conflict_flag([ev("a", "supports")])["conflict"]
    assert rules.conflict_flag([ev("a", "supports"), ev("b", "supports")])["conflict"]
    assert rules.conflict_flag([ev("a", "supports"), ev("a", "refutes")])["conflict"]
    assert not rules.conflict_flag([ev("a", "supports"), ev("a", "refutes", retracted_at="x")])["conflict"]
    assert not rules.conflict_flag([ev("a", "context"), ev(None, "supports")])["conflict"]


def test_queue_score_orders_conflict_and_stakes_first():
    base = dict(age_days=10, review_interval_days=180, changed_pins=0)
    calm = rules.queue_score(conflict=False, stakes="low", **base)[0]
    risky = rules.queue_score(conflict=False, stakes="high", **base)[0]
    clash = rules.queue_score(conflict=True, stakes="high", **base)[0]
    assert calm < risky < clash
    assert "충돌" in rules.queue_score(conflict=True, stakes="mid", **base)[1]


def test_actor_resolution_for_auth_transition():
    assert resolve_actor(ANON_OPEN, " 홍길동 ") == ("홍길동", "anonymous")
    with pytest.raises(ValueError):
        resolve_actor(ANON_OPEN, None)
    senior = Principal(sub="s", name="real-name", roles=frozenset({"senior"}), auth_via="apikey")
    assert resolve_actor(senior, "forged") == ("real-name", "apikey")
