from types import SimpleNamespace

from app.failure_buckets.draft import bucket_body_md, bucket_draft, compute_confidence


def test_compute_confidence_default_no_evidence():
    assert compute_confidence(0, 0) == 0.5


def test_compute_confidence_support_raises():
    assert compute_confidence(1, 0) > 0.5


def test_compute_confidence_counter_lowers():
    assert compute_confidence(0, 1) < 0.5


def test_bucket_body_md_includes_signals():
    body = bucket_body_md(
        bucket_name="LB idle-timeout RST",
        protocol="TCP",
        symptom="다운로드 중 연결 끓김",
        discriminating_signals=["RST 직전 idle 60초 이상"],
        counter_signals=["재전송 다수 관찰"],
        root_cause="LB 세션 idle timeout",
        recommended_action="keepalive 간격을 idle timeout보다 짧게 설정",
    )
    assert "RST 직전 idle 60초 이상" in body
    assert "재전송 다수 관찰" in body
    assert "LB 세션 idle timeout" in body


def _make_row(**overrides):
    base = dict(
        id="abcdef12-3456-7890-abcd-ef1234567890",
        bucket_name="LB idle-timeout RST",
        protocol="TCP",
        symptom="증상",
        discriminating_signals=["신호1"],
        counter_signals=[],
        root_cause="원인",
        recommended_action="조치",
        confidence=0.5,
        support_count=0,
        counter_count=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_bucket_draft_hash_stable_when_only_counts_change():
    row_a = _make_row(confidence=0.5, support_count=0, counter_count=0)
    row_b = _make_row(confidence=0.75, support_count=2, counter_count=0)
    draft_a = bucket_draft(row_a)
    draft_b = bucket_draft(row_b)
    assert draft_a.content_hash == draft_b.content_hash
    assert draft_a.source_type == "failure_bucket"
    assert draft_a.external_id == "FB-abcdef12-3456-7890-abcd-ef1234567890"
    assert draft_a.domain is None
    assert draft_a.evidence_grade == "machine"
