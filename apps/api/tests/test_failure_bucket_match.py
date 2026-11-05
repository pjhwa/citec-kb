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


# ---- ko/en term normalisation + evidence (insight-ops request 2026-11-05) ----------------

_QUORUM = {
    "id": "q", "bucket_name": "suicide 정책 동시 fencing", "confidence": 0.5,
    "discriminating_signals": [
        "no-quorum-policy=suicide 설정과 Quorum lost 기록",
        "양 노드 동시 fencing 후 reboot",
        "DRBD split-brain 감지",
        "corosync 토큰 타임아웃",
    ],
    "counter_signals": [],
}


def test_korean_rewrites_match_only_through_listed_term_groups():
    r = rank_buckets(["양 노드 자기 펜싱 후 재부팅"], "", [_QUORUM])[0]
    assert r["matched_signals"] == ["양 노드 동시 fencing 후 reboot"]
    pairs = {o["token"]: o for o in r["match_evidence"][0]["overlap"]}
    assert pairs["fencing"]["observed_as"] == ["펜싱"] and pairs["fencing"]["signal_as"] == ["fencing"]
    assert pairs["fencing"]["via_term_group"] and pairs["reboot"]["via_term_group"]


def test_korean_particles_and_hyphenated_forms_normalise():
    r = rank_buckets(["클러스터가 쿼럼을 잃음"], "DRBD 스플릿 브레인", [_QUORUM])[0]
    assert "DRBD split-brain 감지" in r["matched_signals"]
    assert rank_buckets(["split brain 발생"], "", [_QUORUM])[0]["matched_signals"] == []  # 1 token of 3: not half


def test_no_semantic_similarity_unlisted_words_do_not_match():
    r = rank_buckets(["노드가 서로를 격리시킨 뒤 재시작"], "", [_QUORUM])[0]  # 'fencing' paraphrase, not a listed term
    assert r["matched_signals"] == []


def test_symptom_text_participates_after_normalisation():
    r = rank_buckets([], "DRBD 스플릿 브레인", [_QUORUM])[0]
    assert "DRBD split-brain 감지" in r["matched_signals"]


def test_label_basis_documents_why_one_of_four_is_not_possible():
    r = rank_buckets(["양 노드 자기 펜싱 후 재부팅"], "", [_QUORUM])[0]
    b = r["label_basis"]
    assert r["label"] == "비권고" and r["confidence"] == 0.35
    assert (b["matched"], b["signals_total"], b["ratio_denominator"]) == (1, 4, 4)
    assert b["matched_needed_for_조건부"] == 2 and b["matched_needed_for_가능"] == 4
    assert len(b["unmatched_signals"]) == 3
    # a refined (higher confidence) bucket reaches 조건부 with the same single match
    hi = dict(_QUORUM, confidence=0.9)
    assert rank_buckets(["양 노드 자기 펜싱 후 재부팅"], "", [hi])[0]["label"] == "조건부"


def test_counter_signal_still_demotes_with_normalisation():
    b = dict(_QUORUM, counter_signals=["정상 failover 후 재기동"])
    r = rank_buckets(["양 노드 자기 펜싱 후 재부팅", "정상 페일오버 후 재기동"], "", [b])[0]
    assert r["contradicted"] == ["정상 failover 후 재기동"] and r["label"] == "비권고"
    assert r["contradicted_evidence"][0]["overlap"]
