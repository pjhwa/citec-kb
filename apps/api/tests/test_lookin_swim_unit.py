"""LOOKIN 기술지원 ↔ SWIM 매칭, 개발·운영 동기화 - DB 없는 테스트."""

import json
from datetime import date

from app.matching.lookin_swim import support_date
from app.matching.sync_matches import diff

P24 = "[CI-TEC] > Ⅲ. CI-TEC 기술지원 ('20년~'26년) > [Backup] 24년 기술지원 > x"


def test_support_date_from_title_and_year_folder():
    assert support_date("[11/08] 카드 모니모 redis cli timeout 이슈", P24) == date(2024, 11, 8)
    assert support_date("[9/21] WekaIO 성능", P24) == date(2024, 9, 21)
    assert support_date("[10/08 ~ 10/27] 2024 하반기 GSAT", P24) == date(2024, 10, 8)
    assert support_date("[09/25-11/12] 개정", P24) == date(2024, 9, 25)


def test_support_date_rejects_missing_or_invalid():
    assert support_date("[21-04] DS Re-architecture 제안", P24) is None   # 월만 있는 표기
    assert support_date("[02/30] 없는 날짜", P24) is None
    assert support_date("[06/25] 제목", "[CI-TEC] > 기타") is None        # 연도 폴더 없음
    assert support_date("7/12 tx ring full count", P24) is None            # 대괄호 없음


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _doc(st, e, h="h"):
    return {"kind": "doc", "source_type": st, "external_id": e, "content_hash": h}


def _m(post, inc, status="review", method="content"):
    return {"kind": "match", "table": "dogether_swim_matches", "post_source_type": "dogether_preventive",
            "post": post, "incident": inc, "method": method, "status": status, "score": 0.5, "rank": 1,
            "signals": {}, "matcher_version": "1"}


def test_diff_counts_docs_and_matches_and_movable(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write(a, [{"kind": "meta"}, _doc("incident_reports", "I1"), _doc("incident_reports", "I2"),
               _doc("dogether_preventive", "dogether-1", "x"),
               _m("dogether-1", "I1"), _m("dogether-1", "I2", "confirmed", "manual")])
    _write(b, [{"kind": "meta"}, _doc("incident_reports", "I1"), _doc("incident_reports", "I3"),
               _doc("dogether_preventive", "dogether-1", "y"),
               _m("dogether-1", "I1", "rejected", "manual")])
    out = diff(str(a), str(b))
    inc = out["docs"]["incident_reports"]
    assert (inc["only_A"], inc["only_B"]) == (1, 1) and inc["only_A_sample"] == ["I2"]
    assert out["docs"]["dogether_preventive"]["content_differs"] == 1
    mt = out["matches"]["dogether_swim_matches"]
    assert mt["only_A"] == 1 and mt["status_differs"] == 1
    # I2는 B에 장애 문서가 없어 옮길 수 없다
    assert mt["only_A_movable_to_B"] == 0
