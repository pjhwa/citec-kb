"""Pure-function unit tests for app.retrieval.search (no DB).

P1-A (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §8).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.retrieval.search import retrieval_trust, title_match_bonus


# --- D06: fts_rank==1 alone must not manufacture "strong" trust from a
# near-zero score (REVIEW.md item 9: "fts_rank==1이면 작은 점수도 strong").


def test_fts_rank1_with_near_zero_score_is_not_strong():
    top = SimpleNamespace(score=0.001, fts_rank=1)
    assert retrieval_trust([top]) != "strong"


def test_fts_rank1_with_near_zero_score_is_weak():
    """0.001 is below the medium floor too — the fts_rank=1 bump only
    applies to a result that has already cleared the medium floor."""
    top = SimpleNamespace(score=0.001, fts_rank=1)
    assert retrieval_trust([top]) == "weak"


def test_fts_rank1_at_medium_score_is_lifted_to_strong():
    """fts_rank==1 still means something — it lifts an already-medium score
    up one level, it just can't create trust out of nothing."""
    top = SimpleNamespace(score=0.03, fts_rank=1)
    assert retrieval_trust([top]) == "strong"


def test_medium_score_without_fts_rank1_stays_medium():
    top = SimpleNamespace(score=0.03, fts_rank=5)
    assert retrieval_trust([top]) == "medium"


def test_high_score_is_strong_regardless_of_fts_rank():
    top = SimpleNamespace(score=0.06, fts_rank=None)
    assert retrieval_trust([top]) == "strong"


def test_low_score_without_fts_rank1_is_weak():
    top = SimpleNamespace(score=0.005, fts_rank=3)
    assert retrieval_trust([top]) == "weak"


def test_empty_results_is_empty():
    assert retrieval_trust([]) == "empty"


# --- REVIEW.md item 5 / §8: the exact-title-match bonus used to be a flat
# +0.25, ~7-8x the max attainable 2-list RRF score (~0.033 at k=60) — so it
# unconditionally overrode fusion ranking for any title-token match,
# including from a heavily relaxed multi-query subquery. title_match_bonus
# scales it to the batch's own top score instead of a fixed constant.


def test_title_bonus_is_capped_relative_to_weak_batch_scores():
    """A batch of weak RRF scores (typical of a relaxed multi-query
    subquery) must not receive the old flat 0.25 — that's ~50x these
    scores, not a modest boost."""
    weak_scores = [0.0006, 0.0004, 0.0002]
    bonus = title_match_bonus(weak_scores)
    assert bonus < 0.25
    assert bonus == 0.05  # floor, since 1.2*top (0.00072) is below it


def test_title_bonus_still_reaches_old_ceiling_for_strong_batches():
    """A batch that already has strong scores (multiple lists agreeing,
    checklist/exact boosts) keeps close to the original bonus size — title
    match still meaningfully wins ties among genuinely strong candidates."""
    strong_scores = [0.22, 0.18, 0.1]
    bonus = title_match_bonus(strong_scores)
    assert bonus == 0.25  # ceiling: 1.2*0.22=0.264 > 0.25


def test_title_bonus_scales_between_floor_and_ceiling():
    scores = [0.1]
    bonus = title_match_bonus(scores)
    assert bonus == pytest.approx(0.12)  # 1.2 * 0.1, between floor/ceiling


def test_title_bonus_empty_batch_is_floor():
    assert title_match_bonus([]) == 0.05


def test_query_years_extraction():
    from app.retrieval.search import _query_years

    assert _query_years("2026년 SCP v2 Multi-AZ") == {"2026"}
    assert _query_years("2025 년과 2026년 비교") == {"2025", "2026"}
    assert _query_years("SCP 12026 년") == set()
    assert _query_years("지난해 장애") == set()


class _FakeSession:
    def __init__(self, dfs):
        self.dfs = dfs
        self.calls = 0

    def scalar(self, stmt):
        self.calls += 1
        return self.dfs[self._tok(stmt)]

    @staticmethod
    def _tok(stmt):
        # params hold the 'simple' regconfig and the token; the token is the other one
        vals = [v for v in stmt.compile().params.values() if v != "simple"]
        return vals[0]


def test_rare_token_variants_pick_rarest_and_skip_absent(monkeypatch):
    import app.retrieval.search as S

    monkeypatch.setattr(S, "_RARE_DF_CACHE", {})
    dfs = {"BFD": 72, "down": 10357, "4시간": 79, "주기": 740, "Redis": 870, "접속": 5470, "불가": 7447, "없는말": 0}
    fs = _FakeSession(dfs)
    out = S.rare_token_variants(fs, "BFD down 4시간 주기 Redis 접속 불가 없는말")
    assert out == ["BFD 4시간", "BFD 4시간 주기"]


def test_rare_token_variants_ignore_short_queries(monkeypatch):
    import app.retrieval.search as S

    monkeypatch.setattr(S, "_RARE_DF_CACHE", {})
    fs = _FakeSession({})
    assert S.rare_token_variants(fs, "Redis timeout 원인") == []
    assert fs.calls == 0


def test_pointer_factor_env(monkeypatch):
    import app.retrieval.search as S

    monkeypatch.delenv("SEARCH_POINTER_FACTOR", raising=False)
    assert S._pointer_factor() == 0.85
    monkeypatch.setenv("SEARCH_POINTER_FACTOR", "1")
    assert S._pointer_factor() == 1.0
    monkeypatch.setenv("SEARCH_POINTER_FACTOR", "abc")
    assert S._pointer_factor() == 0.85
    monkeypatch.setenv("SEARCH_POINTER_FACTOR", "7")
    assert S._pointer_factor() == 1.0
