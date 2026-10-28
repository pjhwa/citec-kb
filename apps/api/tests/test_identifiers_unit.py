"""A6 — 질의 식별자 추출·trust 조정 (DB 불필요)."""

from app.retrieval.identifiers import adjust_trust, identifier_tokens


def test_identifier_tokens_picks_document_ids_only():
    assert identifier_tokens("CITECTS-99999 내용 알려줘") == ["CITECTS-99999"]
    assert identifier_tokens("citects-8888 Redis 장애 사례") == ["citects-8888"]
    assert identifier_tokens("PISAOLNX_02.01.02 점검항목") == ["PISAOLNX_02.01.02"]
    assert identifier_tokens("SWIM 26051461110 원인") == ["26051461110"]
    assert identifier_tokens("confluence_2586686445 페이지") == ["confluence_2586686445"]


def test_korean_particles_directly_attached():
    assert identifier_tokens("CITECTS-2502의 원인") == ["CITECTS-2502"]
    assert identifier_tokens("SWIM 26051461110은 무엇") == ["26051461110"]
    assert identifier_tokens("PISAOLNX_02.01.02를 보여줘") == ["PISAOLNX_02.01.02"]


def test_ordinary_numbers_are_not_identifiers():
    for q in ("2026년 9월 38건", "limit 100000 건", "버전 1.2.3", "포트 8572", "IP 70.222.78.8", "20261028"):
        assert identifier_tokens(q) == [], q


def test_dedup_case_insensitive():
    assert identifier_tokens("CITECTS-1 와 citects-1") == ["CITECTS-1"]


def test_adjust_trust_only_lowers_when_all_missing():
    assert adjust_trust("strong", ["CITECTS-1"], ["CITECTS-1"]) == "weak"
    assert adjust_trust("medium", ["CITECTS-1"], ["CITECTS-1"]) == "weak"
    assert adjust_trust("weak", ["CITECTS-1"], ["CITECTS-1"]) == "weak"
    assert adjust_trust("empty", ["CITECTS-1"], ["CITECTS-1"]) == "empty"
    # 일부만 없으면 그대로(경고만)
    assert adjust_trust("strong", ["CITECTS-1", "CITECTS-2"], ["CITECTS-2"]) == "strong"
    # 식별자가 없거나 모두 존재하면 변화 없음
    assert adjust_trust("strong", [], []) == "strong"
    assert adjust_trust("strong", ["CITECTS-1"], []) == "strong"
