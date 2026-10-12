from app.graph.extract import extract_lexicon_components
from app.lexicon.seed import build_lexicon_map, load_lexicon_seed_file


def test_lexicon_seed_has_gro():
    data = load_lexicon_seed_file()
    cans = {t["canonical"] for t in data["terms"]}
    assert "GRO" in cans
    assert "Redis" in cans


def _real_lexicon_map() -> dict[str, list[str]]:
    """G11 — load_lexicon_map()은 @lru_cache + DB 접근이라 CI에서 직접 못
    부르지만, build_lexicon_map()은 순수 함수라 실제 core.json을 그대로
    넣어 테스트할 수 있다. 과거엔 테스트가 "넷앱"을 직접 끼워 넣은 가짜
    lexicon_map으로만 검증해, 실제 seed 파일에 그 별칭이 있는지는 전혀
    확인하지 못했다(2차 핸드오프에서 지적된 바로 그 간극)."""
    return build_lexicon_map(load_lexicon_seed_file()["terms"])


def test_real_seed_netapp_aliases_share_canonical():
    lexicon_map = _real_lexicon_map()
    assert lexicon_map["netapp"][0] == "NetApp"
    assert lexicon_map["넷앱"][0] == "NetApp"
    assert lexicon_map["NetApp".lower()][0] == "NetApp"


def test_real_seed_netapp_cluster_multiword_variant_matches_in_body():
    lexicon_map = _real_lexicon_map()
    doc = {"id": "d1", "body_md": "NetApp Cluster 장애 발생"}
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    canonicals = {e.target_value for e in edges}
    assert "NetApp_Cluster" in canonicals


def test_real_seed_single_syllable_hangul_variant_excluded_from_graph_matching():
    lexicon_map = _real_lexicon_map()
    assert "행" in lexicon_map  # seed/검색 FTS에는 그대로 남아 있어야 함
    doc = {"id": "d1", "body_md": "표의 1행을 확인하고 행정 절차를 진행했다"}
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    assert edges == []
