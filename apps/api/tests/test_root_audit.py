"""Root-coverage audit: which uncovered folders look incident-related."""

from __future__ import annotations

import asyncio

from app.confluence.root_audit import find_root_candidates

TREE = {
    "home": [
        {"id": "1", "title": "005. 이슈/문제/KDB/SOP"},
        {"id": "2", "title": "B-18. 셀별 공간"},
        {"id": "3", "title": "장애 보고서"},
        {"id": "4", "title": "회의록"},
    ],
    "2": [{"id": "21", "title": "SRE파트"}],
    "21": [{"id": "211", "title": "운영매뉴얼/트러블슈팅"}],
    "1": [{"id": "11", "title": "하위 SOP"}],
}


async def _children(pid):
    return TREE.get(pid, [])


def _run(covered, depth):
    return asyncio.run(find_root_candidates("home", _children, covered, max_depth=depth))


def test_depth_one_lists_only_uncovered_matching_children():
    got = _run({"1"}, 1)
    assert [c["page_id"] for c in got] == ["3"]  # 1 is covered, 2/4 don't match


def test_covered_root_is_not_descended_into():
    got = _run({"1"}, 3)
    assert "11" not in [c["page_id"] for c in got]


def test_deeper_pass_finds_folder_a_depth_one_pass_misses():
    assert "211" not in [c["page_id"] for c in _run(set(), 1)]
    deep = _run(set(), 3)
    hit = next(c for c in deep if c["page_id"] == "211")
    assert hit["depth"] == 3
    assert hit["path"] == "B-18. 셀별 공간 > SRE파트 > 운영매뉴얼/트러블슈팅"


def test_registering_the_root_removes_the_candidate():
    assert "211" not in [c["page_id"] for c in _run({"211"}, 3)]
