import os

import pytest

from app.auth.principal import Principal
import app.masking.audit as A

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")


def test_row_values_never_include_masked_text():
    row = A.build_row(
        principal=Principal(sub="s1", name="n1", roles=frozenset({"senior", "viewer"}), auth_via="oidc"),
        level="senior", method="GET", path="/v1/tickets/X-1",
        doc_refs=["X-1"], counts={"pii": 2, "net": 1}, client_ip="10.1.1.1", outcome="granted",
    )
    assert row["sub"] == "s1" and row["roles"] == "senior,viewer"
    assert row["counts"] == {"pii": 2, "net": 1} and row["outcome"] == "granted"
    assert set(row) == {"sub", "name", "roles", "level", "method", "path", "doc_refs", "counts", "client_ip", "outcome"}


def test_path_and_refs_are_truncated():
    row = A.build_row(
        principal=Principal(sub="s", name="n", roles=frozenset({"admin"}), auth_via="apikey"),
        level="senior", method="GET", path="/v1/" + "x" * 500, doc_refs=[str(i) for i in range(100)],
        counts={}, client_ip=None, outcome="denied",
    )
    assert len(row["path"]) <= 256 and len(row["doc_refs"]) <= 50


@pytest.mark.skipif(not _TEST_DSN, reason="needs scratch DB (CONFLUENCE_SYNC_TEST_DATABASE_URL)")
def test_insert_roundtrip():
    from sqlalchemy import select
    from app.db.models import UnmaskAuditLog
    from app.db.session import session_scope

    A.write_unmask_audit(
        principal=Principal(sub="t", name="t", roles=frozenset({"admin"}), auth_via="apikey"),
        level="senior", method="GET", path="/v1/x", doc_refs=["1"], counts={"pii": 1}, client_ip="127.0.0.1",
        outcome="granted",
    )
    with session_scope() as s:
        assert s.scalar(select(UnmaskAuditLog).order_by(UnmaskAuditLog.id.desc()).limit(1)).outcome == "granted"
