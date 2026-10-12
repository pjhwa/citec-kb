from app.graph.extract import (
    Edge,
    extract_business_entities,
    extract_hierarchy,
    extract_lexicon_components,
    extract_references,
    extract_structured_components,
)


def test_extract_hierarchy_returns_only_direct_parent_edge():
    # 운영 재현 입력(G01/G02): id는 source-prefixed, ancestor_ids는 raw page id
    # 체인(root...parent,self). 직계 부모(마지막 조상) 1개만 edge가 돼야 한다.
    doc = {
        "id": "confluence_map:604365046",
        "external_id": "604365046",
        "metadata": {"ancestor_ids": ["595824602", "604365026", "604365046"]},
    }
    index = {"604365026": [{"document_id": "confluence_map:604365026", "status": "active",
                             "evidence_grade": "C", "source_type": "confluence_map"}]}
    edges = extract_hierarchy(doc, page_identity_index=index)
    assert edges == [
        Edge(rel_type="PARENT_OF", target_label="Document", target_key="id",
             target_value="confluence_map:604365026", tag="EXTRACTED"),
    ]


def test_extract_hierarchy_returns_empty_when_no_ancestor_ids():
    doc = {"id": "confluence_map:d1", "external_id": "d1", "metadata": {}}
    assert extract_hierarchy(doc) == []


def test_extract_hierarchy_excludes_self_via_raw_external_id():
    # ancestor_ids의 마지막 원소는 raw page id(=external_id)이지 접두사 붙은
    # doc["id"]가 아니다 — 이 비교가 실패하면 self가 별도 유령 Document로
    # 생성된다(G01 재현).
    doc = {"id": "confluence_map:d1", "external_id": "d1", "metadata": {"ancestor_ids": ["d1"]}}
    assert extract_hierarchy(doc) == []


def test_extract_hierarchy_unresolved_parent_becomes_unresolved_page_not_document():
    doc = {
        "id": "confluence_map:604365046",
        "external_id": "604365046",
        "metadata": {"ancestor_ids": ["595824602", "604365026", "604365046"]},
    }
    edges = extract_hierarchy(doc, page_identity_index={})
    assert edges == [
        Edge(rel_type="PARENT_OF", target_label="UnresolvedPage", target_key="external_id",
             target_value="604365026", tag="EXTRACTED"),
    ]


def test_extract_structured_components_from_issue_frame():
    doc = {"id": "d1", "source_type": "incident_reports"}
    issue_frame = {"components": ["Redis", "Network"]}
    edges = extract_structured_components(doc, issue_frame=issue_frame, checkitem=None)
    assert edges == [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="Redis", tag="EXTRACTED"),
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="Network", tag="EXTRACTED"),
    ]


def test_extract_structured_components_from_checkitem_area():
    doc = {"id": "d2", "source_type": "checkitem"}
    checkitem = {"area": "3PAR", "category_1": "구성"}
    edges = extract_structured_components(doc, issue_frame=None, checkitem=checkitem)
    assert edges == [
        Edge(rel_type="HAS_COMPONENT", target_label="Component", target_key="canonical_name",
             target_value="3PAR", tag="EXTRACTED"),
    ]
    # category_1("구성"/"Configuration" 등 5개 PISA 평가축)은 컴포넌트가 아니므로 안 나온다


def test_extract_structured_components_returns_empty_when_no_frame_or_checkitem():
    doc = {"id": "d3", "source_type": "tech_repo"}
    assert extract_structured_components(doc, issue_frame=None, checkitem=None) == []


def test_extract_structured_components_dedupes_empty_strings():
    doc = {"id": "d4", "source_type": "incident_reports"}
    issue_frame = {"components": ["Redis", "", "Redis"]}
    edges = extract_structured_components(doc, issue_frame=issue_frame, checkitem=None)
    assert [e.target_value for e in edges] == ["Redis"]


def test_extract_business_entities_includes_business_and_platform_only():
    doc = {"id": "d1"}
    document_entities = [
        {"entity_id": "sys:monimo", "entity_type": "business_system"},
        {"entity_id": "sys:scp", "entity_type": "platform"},
        {"entity_id": "sys:redis", "entity_type": "component"},
    ]
    edges = extract_business_entities(doc, document_entities=document_entities)
    assert edges == [
        Edge(rel_type="MENTIONS_ENTITY", target_label="BusinessEntity", target_key="id",
             target_value="sys:monimo", tag="EXTRACTED"),
        Edge(rel_type="MENTIONS_ENTITY", target_label="BusinessEntity", target_key="id",
             target_value="sys:scp", tag="EXTRACTED"),
    ]


def test_extract_business_entities_empty_when_no_rows():
    assert extract_business_entities({"id": "d1"}, document_entities=[]) == []


def test_extract_lexicon_components_matches_canonical_and_variant():
    doc = {"id": "d1", "body_md": "Redis timeout 발생, 레디스 재기동함"}
    lexicon_map = {
        "redis": ["Redis", "레디스", "redis"],
        "레디스": ["Redis", "레디스", "redis"],
        "timeout": ["timeout", "타임아웃", "time-out", "timed out"],
    }
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    canonicals = sorted({e.target_value for e in edges})
    assert canonicals == ["Redis", "timeout"]
    assert all(e.tag == "INFERRED" for e in edges)


def test_extract_lexicon_components_empty_body_returns_empty():
    doc = {"id": "d1", "body_md": ""}
    assert extract_lexicon_components(doc, lexicon_map={"redis": ["Redis"]}) == []


def test_extract_lexicon_components_no_match_returns_empty():
    doc = {"id": "d1", "body_md": "전혀 관련 없는 본문"}
    assert extract_lexicon_components(doc, lexicon_map={"redis": ["Redis"]}) == []


def test_extract_lexicon_components_matches_multi_word_ascii_phrase():
    # G06: 공백 포함 등록 구문("NetApp Cluster")은 과거 토큰 집합 방식으로는
    # 절대 안 매칭됐다.
    doc = {"id": "d1", "body_md": "NetApp Cluster 장애 발생"}
    lexicon_map = {"netapp cluster": ["NetApp_Cluster", "NetApp Cluster"]}
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    assert [e.target_value for e in edges] == ["NetApp_Cluster"]


def test_extract_lexicon_components_matches_korean_variant_with_josa_suffix():
    # G06: "넷앱에서"처럼 조사가 붙으면 \b 경계로는 안 끊긴다 — 한글 변형어는
    # 단순 포함 검사를 쓴다.
    doc = {"id": "d1", "body_md": "넷앱에서 디스크 지연이 발생했다"}
    lexicon_map = {"넷앱": ["NetApp", "netapp", "넷앱"]}
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    assert [e.target_value for e in edges] == ["NetApp"]


def test_extract_lexicon_components_korean_single_syllable_variant_never_matches():
    # "행"(hang) 같은 한글 한 글자짜리 변형어는 왼쪽 경계만으로는 "진행"/"수행"
    # 뿐 아니라 "행정"/"행사"/"행동"/"1행"(표의 행) 같은 흔한 단어·단위 표현도
    # 못 걸러낸다 — 그래프 매칭에서 완전히 제외한다(1차 리뷰에서 재현).
    lexicon_map = {"행": ["hang", "hung", "soft lockup", "행"]}
    for body in [
        "작업을 정상적으로 진행 및 수행했습니다",
        "서버가 행 상태에 빠졌다",  # 왼쪽 경계만으로는 걸렸을 케이스
        "표의 1행을 확인하라",
        "행정 절차를 완료했다",
        "다음 행으로 이동한다",
    ]:
        doc = {"id": "d1", "body_md": body}
        assert extract_lexicon_components(doc, lexicon_map=lexicon_map) == [], body


def test_extract_lexicon_components_korean_single_syllable_variant_still_reachable_via_other_variants():
    # 한 글자 변형어만 빠진다 — 같은 canonical의 다른(두 글자 이상) 변형어는 정상 매칭.
    doc = {"id": "d1", "body_md": "서버가 hung 상태에 빠졌다"}
    lexicon_map = {"행": ["hang", "hung", "soft lockup", "행"], "hung": ["hang", "hung", "soft lockup", "행"]}
    edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
    assert [e.target_value for e in edges] == ["hang"]


def test_extract_lexicon_components_ascii_boundary_avoids_partial_match():
    # "SQL"이 "MySQLX" 같은 더 긴 식별자 내부의 부분 문자열로 오탐되면 안 된다.
    doc = {"id": "d1", "body_md": "MySQLX 드라이버 업데이트"}
    lexicon_map = {"sql": ["SQL"]}
    assert extract_lexicon_components(doc, lexicon_map=lexicon_map) == []


def test_extract_lexicon_components_ascii_boundary_rejects_path_hostname_dotted_key():
    # 2차 리뷰에서 실제 코퍼스로 재현된 회귀 — \\b는 "/","-",".""을 전부 경계로
    # 봐서 과거(_TOKEN_RE, 이 문자들을 토큰에 포함) 안 걸리던 경로/호스트명/점
    # 구분 키 안의 부분 문자열이 새로 오탐됐다.
    cases = [
        ("filesystem", {"fs": ["fs"]}, "설정 확인: /sys/fs/cgroup/memory/ 아래에서"),
        ("filesystem", {"fs": ["fs"]}, "vfs.fs.size[/,pfree] 메트릭 확인"),
        ("filesystem", {"fs": ["fs"]}, "fs.file-max 파라미터를 조정했다"),
        ("MDS", {"mds": ["MDS"]}, "ceph-mds=enabled 로 설정돼 있음"),
        ("SCP", {"scp": ["SCP"]}, "scp-pbupa02 서버에서 DNS 오류 발생"),
    ]
    for canonical, lexicon_map, body in cases:
        doc = {"id": "d1", "body_md": body}
        assert extract_lexicon_components(doc, lexicon_map=lexicon_map) == [], (canonical, body)


def test_extract_lexicon_components_ascii_boundary_still_matches_sentence_final_and_josa():
    # 경계를 좁혔다고 G06의 핵심 이득(조사/문장부호 뒤 매칭)까지 잃으면 안 된다.
    cases = [
        ("Redis", {"redis": ["Redis"]}, "장애 원인은 Redis. 재기동함"),
        ("Redis", {"redis": ["Redis"]}, "Redis가 다운되어 복구함"),
        ("NetApp", {"netapp": ["NetApp"]}, "NetApp에서 지연이 발생했다"),
        ("SQL Server", {"sql server": ["SQL Server"]}, "MS SQL Server 장애 발생"),
        ("NetApp_Cluster", {"netapp cluster": ["NetApp_Cluster", "NetApp Cluster"]}, "NetApp Cluster 장애 발생"),
    ]
    for canonical, lexicon_map, body in cases:
        doc = {"id": "d1", "body_md": body}
        edges = extract_lexicon_components(doc, lexicon_map=lexicon_map)
        assert [e.target_value for e in edges] == [canonical], (canonical, body)


def test_extract_references_finds_ticket_id_and_resolves_via_index():
    doc = {"id": "d1", "body_md": "CITECTS-1234 사례와 유사함. citects-5678도 참고"}
    index = {"citects-1234": "doc-a", "citects-5678": "doc-b"}
    edges = extract_references(doc, external_id_index=index)
    assert sorted(e.target_value for e in edges) == ["doc-a", "doc-b"]
    assert all(e.rel_type == "REFERENCES" and e.target_label == "Document" for e in edges)


def test_extract_references_skips_unresolved_ids():
    doc = {"id": "d1", "body_md": "CITECTS-9999 참고"}
    edges = extract_references(doc, external_id_index={})
    assert edges == []


def test_extract_references_empty_body_returns_empty():
    doc = {"id": "d1", "body_md": ""}
    assert extract_references(doc, external_id_index={"citects-1": "x"}) == []


def test_extract_references_excludes_self_reference():
    doc = {"id": "d1", "external_id": "CITECTS-9999", "body_md": "- **Issue Key**: CITECTS-9999 처리중"}
    index = {"citects-9999": "d1"}
    assert extract_references(doc, external_id_index=index) == []


def test_extract_references_still_includes_other_mentions_alongside_self_mention():
    doc = {"id": "d1", "external_id": "CITECTS-9999", "body_md": "CITECTS-9999 본인 건, CITECTS-1234 유사 사례 참고"}
    index = {"citects-9999": "d1", "citects-1234": "doc-other"}
    edges = extract_references(doc, external_id_index=index)
    assert [e.target_value for e in edges] == ["doc-other"]
