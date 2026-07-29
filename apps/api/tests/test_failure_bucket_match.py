from app.failure_buckets.match import rank_buckets


_LB_BUCKET = {
    "id": "b1",
    "bucket_name": "LB idle-timeout RST",
    "discriminating_signals": ["RST 직전 idle 60초 이상", "FIN 없이 RST"],
    "counter_signals": ["RST 이전 재전송 다수 관찰"],
    "confidence": 0.5,
}

_TLS_BUCKET = {
    "id": "b2",
    "bucket_name": "TLS record 재조립 지연",
    "discriminating_signals": ["TLS record 分割 다수", "재조립 지연"],
    "counter_signals": [],
    "confidence": 0.5,
}


def test_rank_buckets_matches_discriminating_signals():
    results = rank_buckets(
        observed_signals=["RST 직전 idle 62초"],
        symptom="다운로드 중 연결 끓김",
        buckets=[_LB_BUCKET, _TLS_BUCKET],
    )
    assert results[0]["bucket_id"] == "b1"
    assert "RST 직전 idle 60초 이상" in results[0]["matched_signals"]
    assert results[0]["label"] in {"가능", "조건부"}


def test_rank_buckets_penalizes_counter_signal():
    results = rank_buckets(
        observed_signals=["RST 직전 idle 62초", "RST 이전 재전송 다수 관찰"],
        symptom="",
        buckets=[_LB_BUCKET],
    )
    assert results[0]["contradicted"] == ["RST 이전 재전송 다수 관찰"]
    assert results[0]["label"] == "비권고"


def test_rank_buckets_no_match_scores_low():
    results = rank_buckets(
        observed_signals=["완전히 무관한 신호 텍스트"],
        symptom="",
        buckets=[_LB_BUCKET, _TLS_BUCKET],
    )
    assert all(r["confidence"] <= 0.35 for r in results)
