"""Trust engine unit tests."""

from app.trust.engine import assess_trust


def test_empty_retrieval_abstains():
    t = assess_trust(
        retrieval_trust="empty",
        n_hits=0,
        n_citations_used=0,
        answer="",
        context_blobs=[],
    )
    assert t.abstain is True
    assert t.level == "abstain"
    assert "기권" in t.banner


def test_strong_path_with_citations():
    t = assess_trust(
        retrieval_trust="strong",
        n_hits=3,
        n_citations_used=2,
        answer="원인 A입니다 [C1]. 조치 B입니다 [C2].",
        context_blobs=["원인 A 분석", "조치 B 적용"],
    )
    assert t.abstain is False
    assert t.level in {"strong", "medium"}
    assert t.evidence in {"strong", "medium"}


def test_weak_weak_abstains():
    t = assess_trust(
        retrieval_trust="weak",
        n_hits=1,
        n_citations_used=0,
        answer="일반적으로 재시작하면 됩니다. 별다른 근거 없음 장문 답변입니다 여기 더 길게.",
        context_blobs=["완전 다른 내용 XYZ"],
    )
    assert t.abstain is True or t.level in {"weak", "abstain"}
