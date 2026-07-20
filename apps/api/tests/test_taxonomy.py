from app.taxonomy import enrich_draft_fields, infer_domain, infer_environment


def test_infer_environment_scp():
    assert infer_environment("SCP 금융풀 이슈", "워커노드 지연") == "csp"


def test_infer_domain_from_path():
    assert (
        infer_domain("제목", "본문", path_l2="★★분야별 기술 자료★★", path_l3="운영체제 > Linux")
        == "os"
    )


def test_enrich_support_component():
    out = enrich_draft_fields(
        title="장애",
        body="내용",
        source_type="support_history",
        metadata={"Component": "기술지원"},
        path_l2=None,
        path_l3=None,
        environment=None,
        domain=None,
        work_type=None,
    )
    assert out["work_type"] == "기술지원"
