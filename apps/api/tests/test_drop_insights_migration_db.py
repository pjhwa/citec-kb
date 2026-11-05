"""0022: insight documents are purged, other documents survive, upgrade<->downgrade round trip."""

from __future__ import annotations

import os

import pytest

_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="needs scratch DB")


def _alembic(*args):
    import subprocess

    env = {**os.environ, "DATABASE_URL": _DSN}
    r = subprocess.run(["alembic", *args], cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]


def _q(sql):
    import sqlalchemy as sa

    eng = sa.create_engine(_DSN)
    with eng.begin() as c:
        r = c.execute(sa.text(sql))
        return r.all() if r.returns_rows else []


def test_roundtrip_and_purge():
    _alembic("downgrade", "20261105_0021")
    assert _q("select to_regclass('public.insights')")[0][0] == "insights"
    _q("insert into documents(id,source_type,external_id,title,body_md,content_hash,status) "
       "values ('mig-ins','insight','INSIGHT-mig','t','b','h','active'),('mig-keep','tech_repo','MIG-KEEP','t','b','h2','active')")
    _q("insert into insights(id,title,status,promoted_document_id) values ('i1','x','approved','mig-ins')")
    _q("insert into feedback(id,target_type,target_id,rating) values ('f1','insight','i1',1),('f2','answer','a1',1)")
    _alembic("upgrade", "head")
    assert _q("select to_regclass('public.insights')")[0][0] is None
    assert _q("select count(*) from documents where source_type='insight'")[0][0] == 0
    assert _q("select count(*) from documents where id='mig-keep'")[0][0] == 1
    assert [r[0] for r in _q("select target_type from feedback where id in ('f1','f2')")] == ["answer"]
    _alembic("downgrade", "20261105_0021")
    assert _q("select to_regclass('public.insights')")[0][0] == "insights"
    assert _q("select count(*) from insights")[0][0] == 0
    _alembic("upgrade", "head")
    _q("delete from documents where id='mig-keep'")
    _q("delete from feedback where id='f2'")
