from app.synonyms.rules import (
    MAX_GROUP,
    clean_terms,
    is_valid_term,
    static_covers,
    term_present,
    terms_key,
    verify_group,
)


def test_term_present_allows_korean_particles_but_not_ascii_embedding():
    assert term_present("서버 OOM이 발생", "OOM")
    assert term_present("메모리 부족으로 종료", "메모리 부족")
    assert not term_present("ROOMS 점검", "OOM")
    assert term_present("Out  of Memory killer", "out of memory")


def test_korean_one_two_chars_rejected():
    assert not is_valid_term("행")
    assert not is_valid_term("펜싱")
    assert is_valid_term("데드락")
    assert is_valid_term("메모리 부족")


def test_ascii_min_three_chars_and_charset():
    assert not is_valid_term("go")
    assert is_valid_term("OOM")
    assert is_valid_term("oom-killer")
    assert not is_valid_term("a;b drop")
    assert not is_valid_term("")
    assert not is_valid_term("x" * 65)


def test_clean_terms_dedupes_case_insensitive_and_caps():
    out = clean_terms(["OOM", "oom", " Out  of Memory ", "행", "메모리 부족"] + [f"term{i}x" for i in range(10)])
    assert out[:3] == ["OOM", "Out of Memory", "메모리 부족"]
    assert len(out) == MAX_GROUP


def test_terms_key_is_order_and_case_insensitive():
    assert terms_key(["OOM", "메모리 부족"]) == terms_key(["메모리 부족", "oom"])


def test_static_covers_subset_of_static_group():
    assert static_covers(["oom", "메모리 부족"])
    assert not static_covers(["oom", "inode 고갈"])


TEXTS = {
    "T-1": "서버 OOM 발생. 메모리 부족으로 프로세스 종료",
    "T-2": "Out of Memory killer 동작 확인, 메모리 부족 알람",
    "T-3": "디스크 사용률 경고",
}


def test_verify_group_requires_two_verified_tickets():
    # default mode: only T-1 holds two terms -> 1 verified ticket -> reject
    assert verify_group(["OOM", "메모리 부족"], ["T-1", "T-2"], TEXTS) is None


def test_verify_group_accepts_two_tickets_each_with_two_terms():
    texts = dict(TEXTS, **{"T-2": "Out of Memory 발생, OOM killer 로그와 메모리 부족 알람"})
    got = verify_group(["OOM", "메모리 부족"], ["T-1", "T-2"], texts)
    assert got is not None
    terms, verified = got
    assert verified == ["T-1", "T-2"]
    assert terms == ["OOM", "메모리 부족"]


def test_verify_group_rejects_when_under_two_tickets():
    assert verify_group(["OOM", "메모리 부족"], ["T-1"], TEXTS) is None


def test_verify_group_relaxed_mode_counts_single_term_tickets():
    got = verify_group(["OOM", "메모리 부족", "kernel panic"], ["T-1", "T-2"], TEXTS, min_terms_per_ticket=1)
    assert got is not None
    terms, verified = got
    assert verified == ["T-1", "T-2"]
    assert terms == ["OOM", "메모리 부족"]  # kernel panic never appears -> dropped


def test_verify_group_ignores_ids_outside_batch():
    assert verify_group(["OOM", "메모리 부족"], ["NOPE-1", "NOPE-2"], TEXTS) is None
