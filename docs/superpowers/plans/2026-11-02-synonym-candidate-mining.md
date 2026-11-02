# 동의어 후보 마이닝 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fabrix가 티켓 제목·증상에서 동의어 후보를 제안하고, 코드 검증과 사람 승인을 통과한 그룹만 DB를 통해 검색 변형(variants)에 반영한다.

**Architecture:** 새 패키지 `app/synonyms/` — 순수 규칙(`rules`), DB 저장(`store`), 런타임 캐시(`runtime`), 마이너(`mine`), CLI(`cli`). `retrieval/search_synonyms.py`는 코드 그룹 + 런타임(승인) 그룹을 합쳐 쓰고, DB 오류 시 코드 그룹만 쓴다. 승인은 `/v1/lexicon/synonym-candidates` API. `lexicon_version`, 그래프, 임베딩은 건드리지 않는다.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x, alembic, Postgres(FTS `simple`), pytest. LLM은 `app.llm_chat.chat_complete` (Fabrix/OpenRouter 자동 선택).

**Spec:** `docs/superpowers/specs/2026-11-02-synonym-candidate-mining-design.md`

**Spec과의 차이 (구현 시 반영, 스펙 문서도 Task 10에서 갱신):**
1. 스펙 §4-1 "결정적 선별(DF 범위로 용어를 먼저 고름)"은 입력 선별이 아니라 **검증 단계의 DF 필터**로 구현한다. LLM이 한·영 교차 표현을 찾으려면 티켓 원문 맥락이 필요하고, 용어를 미리 잘라 주면 후보를 찾을 수 없기 때문이다.
2. 증거 규칙 "티켓에 두 용어가 존재"는 `--min-terms-per-ticket`(기본 2)로 두었다. 첫 dry-run에서 후보가 너무 적으면 1로 완화할 수 있다 (동의어 쌍은 한 티켓에 같이 안 나오는 경우가 많음).
3. ASCII 용어도 3자 미만은 거부한다 (한글 1~2자 금지 원칙의 확장, 오탐 방지).

**공통 규칙 (모든 Task):**
- 테스트 실행 위치: `cd /home/citec/dev/citec-kb/apps/api`, 명령은 `python -m pytest <path> -q`.
- Task마다 커밋 1개, 메시지는 `feat(synonyms): T<N> …` 형식, 끝에 `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- 운영 DB 쓰기·배포는 하지 않는다. 운영 실행은 사용자가 Task 10 런북대로 수행한다.
- 하드코딩된 정답 ID, 질의별 규칙, 한글 1~2자 용어 금지.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `apps/api/app/synonyms/__init__.py` (신규) | 패키지 표시 |
| `apps/api/app/synonyms/rules.py` (신규) | 순수 함수: 용어 정규화·유효성, `terms_key`, 정적 그룹 포함 여부, 근거 티켓 검증 |
| `apps/api/app/synonyms/store.py` (신규) | DB 접근: upsert/list/상태 전이/승인 그룹 로드 |
| `apps/api/app/synonyms/runtime.py` (신규) | 승인 그룹 TTL 캐시, DB 오류 시 이전 값·빈 목록 폴백 |
| `apps/api/app/synonyms/mine.py` (신규) | 배치 조립, 프롬프트, 응답 파싱, 그룹 검증·후보 생성 |
| `apps/api/app/synonyms/cli.py` (신규) | `mine` / `import` 명령 |
| `apps/api/app/routers/synonym_candidates.py` (신규) | 목록·import·approve/reject/disable |
| `apps/api/app/db/models.py` (수정) | `SearchSynonymCandidate` 모델 |
| `apps/api/alembic/versions/20261102_0017_search_synonym_candidate.py` (신규) | 마이그레이션 |
| `apps/api/app/retrieval/search_synonyms.py` (수정) | 코드 + 런타임 그룹 병합 |
| `apps/api/app/main.py` (수정) | 라우터 등록 |
| `scripts/synonym_mine.sh` (신규) | `docker compose exec` 래퍼 |
| `docs/SYNONYM_MINING_RUNBOOK_20261102.md` (신규) | 운영·개발 실행 절차 |
| `apps/api/tests/test_synonyms_rules.py`, `test_synonyms_runtime.py`, `test_synonyms_mine.py`, `test_synonyms_router.py`, `test_synonyms_store_db.py` (신규) | 테스트 |

---

### Task 1: 순수 규칙 모듈 `rules.py`

**Files:**
- Create: `apps/api/app/synonyms/__init__.py`
- Create: `apps/api/app/synonyms/rules.py`
- Modify: `apps/api/app/retrieval/search_synonyms.py` (`_compile` → `compile_term` 공개 이름, 별칭 유지)
- Test: `apps/api/tests/test_synonyms_rules.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`apps/api/tests/test_synonyms_rules.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_synonyms_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.synonyms'`

- [ ] **Step 3: 구현**

`apps/api/app/synonyms/__init__.py`: 빈 파일 (`"""Search synonym candidate mining and runtime groups."""` 한 줄).

`apps/api/app/retrieval/search_synonyms.py` 상단 `_compile`을 공개 이름으로 바꾼다 (기존 이름은 별칭으로 유지):

```python
def compile_term(term: str) -> re.Pattern[str]:
    esc = r"\s+".join(re.escape(p) for p in term.split())
    return re.compile(rf"(?<![0-9A-Za-z가-힣]){esc}(?![0-9A-Za-z가-힣])", re.I)


_compile = compile_term
```

`apps/api/app/synonyms/rules.py`:

```python
"""Pure rules for synonym candidates (no DB, no LLM)."""

from __future__ import annotations

import re

from app.retrieval.search_synonyms import SYNONYM_GROUPS

MAX_GROUP = 6
MIN_VERIFIED_TICKETS = 2
MAX_TERM_LEN = 64

_HANGUL = re.compile(r"[가-힣]")
_ALLOWED = re.compile(r"^[0-9A-Za-z가-힣][0-9A-Za-z가-힣 ._\-/+]*$")


def norm_term(term: str) -> str:
    return " ".join((term or "").split())


def is_valid_term(term: str) -> bool:
    t = norm_term(term)
    if not t or len(t) > MAX_TERM_LEN or not _ALLOWED.match(t):
        return False
    hangul = len(_HANGUL.findall(t))
    if hangul:
        return hangul >= 3  # no 1–2 character Korean (행→hang false-positive history)
    return len(t) >= 3


def clean_terms(terms: list[str]) -> list[str]:
    """Normalise, drop invalid, dedupe case-insensitively, cap at MAX_GROUP."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms or []:
        t = norm_term(str(raw))
        key = t.lower()
        if key in seen or not is_valid_term(t):
            continue
        seen.add(key)
        out.append(t)
        if len(out) == MAX_GROUP:
            break
    return out


def terms_key(terms: list[str]) -> str:
    return "|".join(sorted({norm_term(t).lower() for t in terms}))


def term_present(text: str, term: str) -> bool:
    """Is ``term`` in ``text``? Evidence check, not the search matcher.

    Korean particles glue onto words ("OOM이", "메모리 부족으로"), so the search
    matcher's Hangul word boundary would wrongly reject real evidence. Terms with
    Hangul are matched as plain case-insensitive substrings; pure ASCII terms
    must not be embedded in a longer ASCII/digit run.
    """
    t = norm_term(term)
    if not t:
        return False
    if _HANGUL.search(t):
        return t.lower() in " ".join((text or "").split()).lower()
    pat = r"(?<![0-9A-Za-z])" + r"\s+".join(re.escape(p) for p in t.split()) + r"(?![0-9A-Za-z])"
    return re.search(pat, text or "", re.I) is not None


def static_covers(terms: list[str]) -> bool:
    """True if every term already sits in one code-defined group."""
    want = {norm_term(t).lower() for t in terms}
    return any(want <= {norm_term(x).lower() for x in g} for g in SYNONYM_GROUPS)


def verify_group(
    terms: list[str],
    cited_ids: list[str],
    texts: dict[str, str],
    *,
    min_terms_per_ticket: int = 2,
) -> tuple[list[str], list[str]] | None:
    """Check LLM-cited tickets against the real ticket text.

    ``texts`` holds only the tickets of the batch that was sent to the LLM, so
    ids it invented are ignored. A ticket is *verified* when at least
    ``min_terms_per_ticket`` group terms occur in it (word-bounded). The group
    is kept only with >= MIN_VERIFIED_TICKETS verified tickets; terms that
    never occur in a verified ticket are dropped, and >= 2 terms must remain.
    Returns ``(kept_terms, verified_ticket_ids)`` or None.
    """
    verified: list[str] = []
    seen_terms: set[str] = set()
    for tid in dict.fromkeys(cited_ids or []):
        text = texts.get(tid)
        if not text:
            continue
        present = {t for t in terms if term_present(text, t)}
        if len(present) >= max(1, min_terms_per_ticket):
            verified.append(tid)
            seen_terms |= present
    if len(verified) < MIN_VERIFIED_TICKETS:
        return None
    kept = [t for t in terms if t in seen_terms]
    if len(kept) < 2:
        return None
    return kept, verified
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_synonyms_rules.py tests/test_b1_code_prefix_synonyms.py -q`
Expected: PASS (기존 B1 테스트 포함 회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/synonyms/__init__.py apps/api/app/synonyms/rules.py apps/api/app/retrieval/search_synonyms.py apps/api/tests/test_synonyms_rules.py
git commit -m "feat(synonyms): T1 pure candidate rules (term validity, evidence verification)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: DB 모델과 마이그레이션

**Files:**
- Modify: `apps/api/app/db/models.py` (파일 끝, `GraphRun` 뒤)
- Create: `apps/api/alembic/versions/20261102_0017_search_synonym_candidate.py`
- Test: `apps/api/tests/test_synonyms_store_db.py` (Task 3에서 작성; 여기서는 모델 import만 확인)

- [ ] **Step 1: 모델 추가**

`models.py` 맨 아래에 추가 (상단에 이미 `ARRAY, JSONB, BigInteger, DateTime, Index, String, Text, UniqueConstraint, func, text`가 import되어 있다. 없는 이름이 있으면 같은 import 블록에 추가):

```python
class SearchSynonymCandidate(Base):
    """LLM-proposed search synonym group awaiting human approval.

    Search-only: never feeds lexicon_version / graph / embeddings.
    """

    __tablename__ = "search_synonym_candidate"
    __table_args__ = (
        UniqueConstraint("terms_key", name="uq_search_synonym_candidate_terms_key"),
        Index("ix_search_synonym_candidate_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    terms: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    terms_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="proposed")
    # proposed | approved | rejected | disabled
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    run_id: Mapped[Optional[str]] = mapped_column(String(64))
    model: Mapped[Optional[str]] = mapped_column(String(128))
    decided_by: Mapped[Optional[str]] = mapped_column(String(128))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 2: 마이그레이션 작성**

`apps/api/alembic/versions/20261102_0017_search_synonym_candidate.py`:

```python
"""add search_synonym_candidate (2026-11-02 Fabrix synonym mining; search-only)

Revision ID: 20261102_0017
Revises: 20261014_0016

New table only. Holds LLM-proposed synonym groups and their approval state.
Does not touch lexicon_terms, so lexicon_version / graph fingerprint are unaffected.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261102_0017"
down_revision: Union[str, None] = "20261014_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_synonym_candidate",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("terms", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("terms_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("terms_key", name="uq_search_synonym_candidate_terms_key"),
    )
    op.create_index("ix_search_synonym_candidate_status", "search_synonym_candidate", ["status"])


def downgrade() -> None:
    op.drop_index("ix_search_synonym_candidate_status", table_name="search_synonym_candidate")
    op.drop_table("search_synonym_candidate")
```

- [ ] **Step 3: 확인**

Run: `python -c "from app.db.models import SearchSynonymCandidate as M; print(M.__tablename__)"`
Expected: `search_synonym_candidate`

Run: `python -m alembic heads` (apps/api에서)
Expected: 단일 head `20261102_0017`

- [ ] **Step 4: 커밋**

```bash
git add apps/api/app/db/models.py apps/api/alembic/versions/20261102_0017_search_synonym_candidate.py
git commit -m "feat(synonyms): T2 search_synonym_candidate table (search-only, no lexicon_version impact)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 저장소 `store.py` (+ 스크래치 DB 테스트)

**Files:**
- Create: `apps/api/app/synonyms/store.py`
- Test: `apps/api/tests/test_synonyms_store_db.py`

- [ ] **Step 1: 실패하는 테스트 작성** (스크래치 DB 필요, 없으면 skip — 기존 `*_db.py` 관례)

```python
"""Synonym candidate store (scratch DB only)."""

from __future__ import annotations

import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _db():
    from app.db import session as db_session
    from app.db.models import SearchSynonymCandidate
    from app.db.session import get_engine, session_scope
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    SearchSynonymCandidate.__table__.create(bind=get_engine(), checkfirst=True)
    with session_scope() as s:
        s.query(SearchSynonymCandidate).delete()
    yield
    with session_scope() as s:
        s.query(SearchSynonymCandidate).delete()
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _item(terms, tickets=("T-1", "T-2")):
    return {"terms": terms, "evidence": {"tickets": list(tickets), "df": {}, "reason": "r"}}


def test_upsert_inserts_then_skips_duplicates():
    from app.synonyms import store

    r1 = store.upsert_candidates([_item(["OOM2x", "메모리 고갈"])], run_id="r1", model="m")
    assert r1 == {"inserted": 1, "skipped": 0, "invalid": 0}
    r2 = store.upsert_candidates([_item(["메모리 고갈", "oom2x"])], run_id="r2", model="m")
    assert r2 == {"inserted": 0, "skipped": 1, "invalid": 0}


def test_upsert_rejects_invalid_terms():
    from app.synonyms import store

    r = store.upsert_candidates([_item(["행", "펜싱"])], run_id="r", model="m")
    assert r["invalid"] == 1 and r["inserted"] == 0


def test_status_transitions_and_runtime_load():
    from app.synonyms import store

    store.upsert_candidates([_item(["OOM2x", "메모리 고갈"])], run_id="r", model="m")
    cid = store.list_candidates(status="proposed")["items"][0]["id"]
    assert store.load_groups(include_proposed=False) == []
    assert store.load_groups(include_proposed=True) == [["OOM2x", "메모리 고갈"]]

    assert store.set_status(cid, "approve", decided_by="tester")["status"] == "approved"
    assert store.load_groups(include_proposed=False) == [["OOM2x", "메모리 고갈"]]
    assert store.set_status(cid, "disable", decided_by="tester")["status"] == "disabled"
    assert store.load_groups(include_proposed=False) == []


def test_illegal_transition_raises():
    from app.synonyms import store

    store.upsert_candidates([_item(["OOM2x", "메모리 고갈"])], run_id="r", model="m")
    cid = store.list_candidates(status="proposed")["items"][0]["id"]
    with pytest.raises(store.TransitionError):
        store.set_status(cid, "disable", decided_by="t")  # proposed -> disabled not allowed
    with pytest.raises(store.NotFound):
        store.set_status(999999, "approve", decided_by="t")
```

- [ ] **Step 2: 실패 확인**

Run: `CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch dsn> python -m pytest tests/test_synonyms_store_db.py -q`
Expected: FAIL — `cannot import name 'store'` (DSN이 없으면 skip되므로, 개발 서버 스크래치 DB 사용: `scripts/scratch_db_test.sh` 참고)

- [ ] **Step 3: 구현**

`apps/api/app/synonyms/store.py`:

```python
"""DB access for search_synonym_candidate."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import SearchSynonymCandidate
from app.db.session import session_scope
from app.synonyms.rules import clean_terms, terms_key

# action -> (allowed source statuses, new status)
_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "approve": (frozenset({"proposed", "disabled"}), "approved"),
    "reject": (frozenset({"proposed"}), "rejected"),
    "disable": (frozenset({"approved"}), "disabled"),
}


class NotFound(LookupError):
    pass


class TransitionError(ValueError):
    pass


def _row(r: SearchSynonymCandidate) -> dict[str, Any]:
    return {
        "id": r.id,
        "terms": list(r.terms or []),
        "status": r.status,
        "evidence": r.evidence or {},
        "run_id": r.run_id,
        "model": r.model,
        "decided_by": r.decided_by,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def upsert_candidates(
    items: list[dict[str, Any]], *, run_id: str | None, model: str | None
) -> dict[str, int]:
    """Insert new proposed candidates; existing terms_key rows are left untouched."""
    inserted = skipped = invalid = 0
    with session_scope() as session:
        for it in items:
            terms = clean_terms(list(it.get("terms") or []))
            if len(terms) < 2:
                invalid += 1
                continue
            stmt = (
                pg_insert(SearchSynonymCandidate)
                .values(
                    terms=terms,
                    terms_key=terms_key(terms),
                    status="proposed",
                    evidence=it.get("evidence") or {},
                    run_id=run_id,
                    model=model,
                )
                .on_conflict_do_nothing(constraint="uq_search_synonym_candidate_terms_key")
            )
            res = session.execute(stmt)
            if res.rowcount:
                inserted += 1
            else:
                skipped += 1
    return {"inserted": inserted, "skipped": skipped, "invalid": invalid}


def list_candidates(
    *, status: str | None = None, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    with session_scope() as session:
        q = select(SearchSynonymCandidate)
        cq = select(func.count()).select_from(SearchSynonymCandidate)
        if status:
            q = q.where(SearchSynonymCandidate.status == status)
            cq = cq.where(SearchSynonymCandidate.status == status)
        total = int(session.scalar(cq) or 0)
        rows = session.scalars(
            q.order_by(SearchSynonymCandidate.id).limit(limit).offset(offset)
        ).all()
        return {"total": total, "items": [_row(r) for r in rows]}


def set_status(cid: int, action: str, *, decided_by: str, note: str | None = None) -> dict[str, Any]:
    allowed, new_status = _TRANSITIONS[action]
    with session_scope() as session:
        r = session.get(SearchSynonymCandidate, cid)
        if r is None:
            raise NotFound(f"candidate {cid} not found")
        if r.status not in allowed:
            raise TransitionError(f"cannot {action} a {r.status} candidate")
        r.status = new_status
        r.decided_by = decided_by
        r.decided_at = datetime.now(timezone.utc)
        if note is not None:
            r.note = note
        session.flush()
        return _row(r)


def load_groups(*, include_proposed: bool = False) -> list[list[str]]:
    statuses = ["approved"] + (["proposed"] if include_proposed else [])
    with session_scope() as session:
        rows = session.scalars(
            select(SearchSynonymCandidate)
            .where(SearchSynonymCandidate.status.in_(statuses))
            .order_by(SearchSynonymCandidate.id)
        ).all()
        return [list(r.terms) for r in rows]


def existing_keys() -> set[str]:
    with session_scope() as session:
        return set(session.scalars(select(SearchSynonymCandidate.terms_key)).all())
```

- [ ] **Step 4: 통과 확인**

Run: `CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch dsn> python -m pytest tests/test_synonyms_store_db.py -q`
Expected: 4 passed (DSN 없이 실행하면 skip — CI에서도 skip이므로 개발 서버에서 한 번은 반드시 실제로 돌린다)

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/synonyms/store.py apps/api/tests/test_synonyms_store_db.py
git commit -m "feat(synonyms): T3 candidate store (upsert, status transitions, approved-group load)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 런타임 캐시와 `search_synonyms` 병합

**Files:**
- Create: `apps/api/app/synonyms/runtime.py`
- Modify: `apps/api/app/retrieval/search_synonyms.py` (함수 `search_synonyms` 및 모듈 하단)
- Test: `apps/api/tests/test_synonyms_runtime.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
import app.synonyms.runtime as rt
from app.retrieval import search_synonyms as ss


def _reset(monkeypatch, loader):
    rt.reset_cache()
    monkeypatch.setattr(rt, "_load", loader)


def test_runtime_groups_are_merged_into_search_synonyms(monkeypatch):
    _reset(monkeypatch, lambda: [["inode2x", "아이노드 고갈"]])
    assert "아이노드 고갈" in ss.search_synonyms("inode2x 확인")
    assert "inode2x" in ss.search_synonyms("아이노드 고갈 장애")
    rt.reset_cache()


def test_static_groups_still_work_when_runtime_empty(monkeypatch):
    _reset(monkeypatch, lambda: [])
    assert "Out of Memory" in ss.search_synonyms("Linux OOM")
    rt.reset_cache()


def test_db_failure_falls_back_to_empty_and_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    _reset(monkeypatch, boom)
    assert rt.runtime_groups() == []
    assert "Out of Memory" in ss.search_synonyms("Linux OOM")
    rt.reset_cache()


def test_failure_keeps_previous_good_value(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return [["inode2x", "아이노드 고갈"]]
        raise RuntimeError("db down")

    _reset(monkeypatch, flaky)
    monkeypatch.setattr(rt, "_TTL_S", 0.0)  # force reload every call
    assert rt.runtime_groups() == [["inode2x", "아이노드 고갈"]]
    assert rt.runtime_groups() == [["inode2x", "아이노드 고갈"]]
    rt.reset_cache()


def test_ttl_caches_between_calls(monkeypatch):
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return []

    _reset(monkeypatch, loader)
    rt.runtime_groups()
    rt.runtime_groups()
    assert calls["n"] == 1
    rt.reset_cache()


def test_invalid_db_groups_are_filtered(monkeypatch):
    _reset(monkeypatch, lambda: [["행", "펜싱"], ["inode2x", "아이노드 고갈"]])
    assert rt.runtime_groups() == [["inode2x", "아이노드 고갈"]]
    rt.reset_cache()
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_synonyms_runtime.py -q`
Expected: FAIL — `ModuleNotFoundError: app.synonyms.runtime`

- [ ] **Step 3: 구현**

`apps/api/app/synonyms/runtime.py`:

```python
"""Approved synonym groups from the DB, TTL-cached; never breaks search."""

from __future__ import annotations

import logging
import os
import time

from app.synonyms.rules import clean_terms

logger = logging.getLogger("citec.synonyms.runtime")

_TTL_S = float(os.environ.get("SEARCH_SYNONYMS_TTL_S", "60") or 60)
_cache: tuple[float, list[list[str]]] | None = None


def _include_proposed() -> bool:
    # Dev/gate only: lets the eval gate measure proposed candidates before approval.
    return os.environ.get("SEARCH_SYNONYMS_INCLUDE_PROPOSED", "").strip() in ("1", "true", "yes")


def _load() -> list[list[str]]:
    from app.synonyms import store

    return store.load_groups(include_proposed=_include_proposed())


def reset_cache() -> None:
    global _cache
    _cache = None


def runtime_groups() -> list[list[str]]:
    """Valid DB groups. On any DB error: previous good value, else []."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _TTL_S:
        return _cache[1]
    prev = _cache[1] if _cache is not None else []
    try:
        groups = [g for g in (clean_terms(g) for g in _load()) if len(g) >= 2]
    except Exception as exc:  # noqa: BLE001 — search must never fail because of this
        logger.warning("synonym runtime load failed: %s", exc)
        groups = prev
    _cache = (now, groups)
    return groups
```

`search_synonyms.py` 하단(함수 `search_synonyms` 이후 포함)을 다음으로 교체한다. 모듈 상단의 `SYNONYM_GROUPS`, `compile_term`은 그대로 두고, 기존 `_PATTERNS`, `_PATTERNS_BY_TERM`, 기존 `search_synonyms` 정의를 삭제한 뒤:

```python
_PAT_CACHE: dict[str, re.Pattern[str]] = {}


def _pat(term: str) -> re.Pattern[str]:
    p = _PAT_CACHE.get(term)
    if p is None:
        if len(_PAT_CACHE) > 4096:
            _PAT_CACHE.clear()
        p = _PAT_CACHE[term] = compile_term(term)
    return p


def _all_groups() -> list[list[str]]:
    from app.synonyms.runtime import runtime_groups

    return SYNONYM_GROUPS + runtime_groups()


def search_synonyms(q: str) -> list[str]:
    """Other members of every group (code + approved DB) that has a member in ``q``."""
    text = q or ""
    groups = _all_groups()
    hit = [gi for gi, g in enumerate(groups) if any(_pat(t).search(text) for t in g)]
    out: list[str] = []
    for gi in hit:
        for t in groups[gi]:
            if not _pat(t).search(text) and t not in out:
                out.append(t)
    return out
```

(`runtime.py`가 `rules.py`를 import하고 `rules.py`가 `search_synonyms`를 import하므로, 순환을 피하려고 `_all_groups` 안에서 지연 import한다.)

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_synonyms_runtime.py tests/test_b1_code_prefix_synonyms.py tests/test_synonyms_rules.py tests/test_retrieval_search_pure.py -q`
Expected: PASS (기존 B1·검색 순수 테스트 회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/synonyms/runtime.py apps/api/app/retrieval/search_synonyms.py apps/api/tests/test_synonyms_runtime.py
git commit -m "feat(synonyms): T4 runtime approved-group cache merged into search_synonyms (DB failure-safe)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 마이너 코어 `mine.py` (프롬프트·파싱·검증)

**Files:**
- Create: `apps/api/app/synonyms/mine.py`
- Test: `apps/api/tests/test_synonyms_mine.py`

- [ ] **Step 1: 실패하는 테스트 작성** (LLM·DB 모두 가짜)

```python
import json

from app.synonyms import mine

TICKETS = [
    mine.Ticket("T-1", "inode2x 경고", "아이노드 고갈", "서버 inode2x 경고 발생. 아이노드 고갈로 파일 생성 불가"),
    mine.Ticket("T-2", "아이노드 고갈 알람", "", "inode2x 사용률 100% 아이노드 고갈 알람"),
    mine.Ticket("T-3", "디스크 경고", "", "디스크 사용률 경고"),
]


def test_build_messages_contains_ids_and_json_contract():
    msgs = mine.build_messages(TICKETS)
    assert msgs[0]["role"] == "user"
    text = msgs[0]["content"]
    assert "[T-1]" in text and "[T-3]" in text
    assert '"groups"' in text


def test_parse_response_handles_fences_and_noise():
    raw = "설명\n```json\n" + json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1"], "reason": "r"}]}) + "\n```"
    assert mine.parse_response(raw) == [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1"], "reason": "r"}]
    assert mine.parse_response("not json") == []
    assert mine.parse_response('{"groups": "x"}') == []


class FakeDF:
    def __init__(self, table):
        self.table = table

    def __call__(self, term):
        return self.table.get(term, 0)


def _params(**kw):
    base = dict(df_min=3, df_max=1500, min_terms_per_ticket=2, existing_keys=set())
    base.update(kw)
    return mine.Params(**base)


def test_process_groups_accepts_verified_group():
    groups = [{"terms": ["inode2x", "아이노드 고갈", "행"], "tickets": ["T-1", "T-2"], "reason": "r"}]
    items, stats = mine.process_groups(
        groups, TICKETS, term_df=FakeDF({"inode2x": 50, "아이노드 고갈": 40}), params=_params()
    )
    assert len(items) == 1
    assert items[0]["terms"] == ["inode2x", "아이노드 고갈"]
    assert items[0]["evidence"]["tickets"] == ["T-1", "T-2"]
    assert items[0]["evidence"]["df"] == {"inode2x": 50, "아이노드 고갈": 40}
    assert stats["accepted"] == 1


def test_process_groups_rejects_by_reason():
    df = FakeDF({"inode2x": 50, "아이노드 고갈": 40})
    groups = [
        {"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1"], "reason": "x"},       # 1 verified ticket
        {"terms": ["inode2x", "없는용어들"], "tickets": ["T-1", "T-2"], "reason": "x"},   # term not in tickets
        {"terms": ["행", "펜싱"], "tickets": ["T-1", "T-2"], "reason": "x"},              # invalid terms
    ]
    items, stats = mine.process_groups(groups, TICKETS, term_df=df, params=_params())
    assert items == []
    assert stats["accepted"] == 0 and stats["rej_evidence"] == 2 and stats["rej_terms"] == 1


def test_process_groups_rejects_term_that_is_too_common():
    groups = [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    items, stats = mine.process_groups(
        groups, TICKETS, term_df=FakeDF({"inode2x": 9999, "아이노드 고갈": 40}), params=_params()
    )
    assert items == [] and stats["rej_df"] == 1


def test_process_groups_skips_existing_key():
    from app.synonyms.rules import terms_key

    groups = [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    p = _params(existing_keys={terms_key(["inode2x", "아이노드 고갈"])})
    items, stats = mine.process_groups(groups, TICKETS, term_df=FakeDF({"inode2x": 5, "아이노드 고갈": 5}), params=p)
    assert items == [] and stats["rej_existing"] == 1


def test_process_groups_skips_static_and_existing():
    texts = [
        mine.Ticket("T-1", "t", "", "kernel panic 발생 커널 패닉 로그"),
        mine.Ticket("T-2", "t", "", "커널 패닉 and kernel panic"),
    ]
    g = [{"terms": ["kernel panic", "커널 패닉"], "tickets": ["T-1", "T-2"], "reason": "x"}]
    items, stats = mine.process_groups(g, texts, term_df=FakeDF({"kernel panic": 9, "커널 패닉": 9}), params=_params())
    assert items == [] and stats["rej_static"] == 1


def test_mine_batch_uses_injected_chat_and_survives_llm_error():
    def ok_chat(messages, **kw):
        return json.dumps({"groups": [{"terms": ["inode2x", "아이노드 고갈"], "tickets": ["T-1", "T-2"], "reason": "r"}]})

    def bad_chat(messages, **kw):
        raise RuntimeError("fabrix down")

    df = FakeDF({"inode2x": 50, "아이노드 고갈": 40})
    items, stats = mine.mine_batch(TICKETS, chat=ok_chat, term_df=df, params=_params())
    assert stats["accepted"] == 1 and stats["llm_error"] == 0
    items, stats = mine.mine_batch(TICKETS, chat=bad_chat, term_df=df, params=_params())
    assert items == [] and stats["llm_error"] == 1
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_synonyms_mine.py -q`
Expected: FAIL — `ModuleNotFoundError: app.synonyms.mine`

- [ ] **Step 3: 구현**

`apps/api/app/synonyms/mine.py`:

```python
"""Synonym candidate mining: batch -> LLM proposal -> code verification."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from app.synonyms.rules import (
    MAX_GROUP,
    clean_terms,
    static_covers,
    terms_key,
    verify_group,
)

logger = logging.getLogger("citec.synonyms.mine")

SYSTEM_RULES = (
    "당신은 IT 인프라 기술지원 티켓 검색용 동의어 사전 편집자입니다.\n"
    "아래 티켓 목록(제목/증상)에서, 같은 현상·개념을 가리키지만 표기가 다른 기술 용어 묶음"
    "(한글↔영문, 약어↔풀네임, 표기 변형)을 찾아 JSON으로만 답하세요.\n"
    "규칙:\n"
    "- 각 용어는 인용한 티켓의 텍스트에 글자 그대로 등장해야 합니다 (추측·번역 금지).\n"
    "- 한 묶음은 2~6개 용어, 한글 용어는 3자 이상.\n"
    "- 제품명·고객명·사람 이름, 넓은 분류어(장애, 오류, 서버 등)는 제외.\n"
    "- 단순히 같이 자주 나오는 연관어는 동의어가 아닙니다.\n"
    "- 각 묶음에 근거 티켓 ID를 2개 이상 [대괄호] 안의 ID 그대로 적으세요.\n"
    '출력 형식: {"groups":[{"terms":["..",".."],"tickets":["ID1","ID2"],"reason":"한 줄 근거"}]}\n'
    '없으면 {"groups":[]}'
)


@dataclass(frozen=True)
class Ticket:
    external_id: str
    title: str
    symptom: str
    text: str  # title + body, used only for verification


@dataclass
class Params:
    df_min: int = 3
    df_max: int = 1500
    min_terms_per_ticket: int = 2
    existing_keys: set[str] = field(default_factory=set)


def build_messages(tickets: list[Ticket]) -> list[dict[str, str]]:
    lines = []
    for t in tickets:
        sym = (t.symptom or "").replace("\n", " ")[:200]
        lines.append(f"[{t.external_id}] {t.title} | {sym}".rstrip(" |"))
    return [{"role": "user", "content": SYSTEM_RULES + "\n\n티켓 목록:\n" + "\n".join(lines)}]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_response(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            return []
        text = text[i : j + 1]
    try:
        data = json.loads(text)
    except ValueError:
        return []
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("terms"), list):
            out.append(
                {
                    "terms": [str(x) for x in g["terms"]],
                    "tickets": [str(x) for x in (g.get("tickets") or [])],
                    "reason": str(g.get("reason") or "")[:300],
                }
            )
    return out


def _new_stats() -> dict[str, int]:
    return {
        "groups_in": 0,
        "accepted": 0,
        "llm_error": 0,
        "rej_terms": 0,
        "rej_static": 0,
        "rej_existing": 0,
        "rej_evidence": 0,
        "rej_df": 0,
    }


def process_groups(
    groups: list[dict[str, Any]],
    tickets: list[Ticket],
    *,
    term_df: Callable[[str], int],
    params: Params,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = _new_stats()
    texts = {t.external_id: t.text for t in tickets}
    items: list[dict[str, Any]] = []
    seen = set(params.existing_keys)
    for g in groups:
        stats["groups_in"] += 1
        terms = clean_terms(g.get("terms") or [])
        if len(terms) < 2:
            stats["rej_terms"] += 1
            continue
        verified = verify_group(
            terms, g.get("tickets") or [], texts, min_terms_per_ticket=params.min_terms_per_ticket
        )
        if verified is None:
            stats["rej_evidence"] += 1
            continue
        kept, ticket_ids = verified
        if static_covers(kept):
            stats["rej_static"] += 1
            continue
        dfs = {t: term_df(t) for t in kept}
        kept = [t for t in kept if params.df_min <= dfs[t] <= params.df_max]
        if len(kept) < 2:
            stats["rej_df"] += 1
            continue
        key = terms_key(kept)
        if key in seen:
            stats["rej_existing"] += 1
            continue
        seen.add(key)
        items.append(
            {
                "terms": kept[:MAX_GROUP],
                "evidence": {
                    "tickets": ticket_ids,
                    "df": {t: dfs[t] for t in kept},
                    "reason": g.get("reason", ""),
                    "min_terms_per_ticket": params.min_terms_per_ticket,
                },
            }
        )
        stats["accepted"] += 1
    return items, stats


def mine_batch(
    tickets: list[Ticket],
    *,
    chat: Callable[..., str],
    term_df: Callable[[str], int],
    params: Params,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        raw = chat(build_messages(tickets), max_tokens=3000, temperature=0.1, timeout=180.0)
    except Exception as exc:  # noqa: BLE001 — one failed batch must not stop the run
        logger.warning("synonym batch LLM failure: %s", exc)
        stats = _new_stats()
        stats["llm_error"] = 1
        return [], stats
    return process_groups(parse_response(raw), tickets, term_df=term_df, params=params)


def iter_batches(tickets: list[Ticket], size: int) -> Iterator[tuple[int, list[Ticket]]]:
    for i in range(0, len(tickets), size):
        yield i // size, tickets[i : i + size]
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_synonyms_mine.py tests/test_synonyms_rules.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/synonyms/mine.py apps/api/tests/test_synonyms_mine.py
git commit -m "feat(synonyms): T5 miner core (prompt, tolerant parse, code-side verification, DF filter)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: DB 연동(티켓 로딩·DF) + CLI

**Files:**
- Modify: `apps/api/app/synonyms/mine.py` (끝에 `load_tickets`, `make_term_df` 추가)
- Create: `apps/api/app/synonyms/cli.py`
- Test: `apps/api/tests/test_synonyms_mine.py` (CLI 체크포인트 테스트 추가)

- [ ] **Step 1: 실패하는 테스트 추가** (`test_synonyms_mine.py` 끝에)

```python
def test_parse_created_year_filter():
    from datetime import date

    assert mine.created_on_or_after({"Created": "2025-03-01 10:00"}, date(2025, 1, 1))
    assert not mine.created_on_or_after({"Created": "2023-03-01"}, date(2025, 1, 1))
    assert mine.created_on_or_after({}, date(2025, 1, 1))  # unknown date -> include


def test_run_mine_checkpoint_resume(tmp_path, monkeypatch):
    from app.synonyms import cli

    tickets = [mine.Ticket(f"T-{i}", f"제목 {i}", "", f"OOM 메모리 부족 {i}") for i in range(5)]
    calls = []

    def chat(messages, **kw):
        calls.append(1)
        return json.dumps({"groups": []})

    out = tmp_path / "res.json"
    kw = dict(
        tickets=tickets, batch_size=2, chat=chat, term_df=lambda t: 10,
        params=mine.Params(), run_id="r1", model="m", out_path=str(out), max_batches=None,
    )
    res = cli.run_mine(**kw)
    assert len(calls) == 3 and res["stats"]["batches_done"] == 3
    calls.clear()
    res2 = cli.run_mine(**kw)  # same out/run_id -> everything already checkpointed
    assert calls == [] and res2["stats"]["batches_done"] == 3
    assert json.loads(out.read_text())["run_id"] == "r1"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_synonyms_mine.py -q`
Expected: FAIL — `module 'app.synonyms.mine' has no attribute 'created_on_or_after'`

- [ ] **Step 3: 구현**

`mine.py` 끝에 추가:

```python
import re as _re
from datetime import date, timedelta

_DATE = _re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def created_on_or_after(meta: dict[str, Any], cutoff: date) -> bool:
    """Created date >= cutoff; tickets with an unparseable date are included."""
    m = _DATE.search(str((meta or {}).get("Created") or ""))
    if not m:
        return True
    try:
        return date(int(m[1]), int(m[2]), int(m[3])) >= cutoff
    except ValueError:
        return True


def load_tickets(*, since_years: float, limit: int | None = None) -> list[Ticket]:
    """Active support_history tickets, ordered by external_id (stable batches)."""
    from sqlalchemy import select

    from app.db.models import Document, IssueFrame
    from app.db.session import session_scope

    cutoff = date.today() - timedelta(days=int(365 * since_years))
    out: list[Ticket] = []
    with session_scope() as session:
        rows = session.execute(
            select(
                Document.external_id, Document.title, Document.body_md,
                Document.metadata_, IssueFrame.symptom,
            )
            .select_from(Document)
            .outerjoin(IssueFrame, IssueFrame.document_id == Document.id)
            .where(Document.source_type == "support_history", Document.status == "active")
            .order_by(Document.external_id)
        ).all()
    for ext, title, body, meta, symptom in rows:
        if not (title or "").strip() or not created_on_or_after(meta or {}, cutoff):
            continue
        sym = (symptom or "").strip() or (body or "").strip()[:200]
        out.append(Ticket(ext, title.strip(), sym, f"{title}\n{body or ''}"))
        if limit and len(out) >= limit:
            break
    return out


def make_term_df() -> Callable[[str], int]:
    """Active-chunk FTS document frequency (capped), same query as search._token_df."""
    from sqlalchemy import func, select

    from app.db.models import Chunk
    from app.db.session import session_scope

    cache: dict[str, int] = {}

    def df(term: str) -> int:
        key = term.lower()
        if key in cache:
            return cache[key]
        with session_scope() as session:
            capped = (
                select(Chunk.id)
                .where(Chunk.is_active.is_(True))
                .where(Chunk.tsv.op("@@")(func.plainto_tsquery("simple", term)))
                .limit(2000)
                .subquery()
            )
            n = int(session.scalar(select(func.count()).select_from(capped)) or 0)
        cache[key] = n
        return n

    return df
```

`apps/api/app/synonyms/cli.py`:

```python
"""CLI: python -m app.synonyms.cli {mine|import} …

mine   -- ask the LLM for synonym candidates, verify in code, write a JSON file
          (dry-run unless --commit). Resumable via <out>.progress.jsonl.
import -- load a mined JSON file into search_synonym_candidate as 'proposed'.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from typing import Any, Callable

from app.synonyms import mine


def _merge_stats(total: dict[str, int], part: dict[str, int]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + int(v)


def run_mine(
    *,
    tickets: list[mine.Ticket],
    batch_size: int,
    chat: Callable[..., str],
    term_df: Callable[[str], int],
    params: mine.Params,
    run_id: str,
    model: str,
    out_path: str,
    max_batches: int | None,
) -> dict[str, Any]:
    progress_path = out_path + ".progress.jsonl"
    done: dict[int, dict[str, Any]] = {}
    if os.path.exists(progress_path):
        with open(progress_path, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("run_id") == run_id:
                    done[int(rec["batch"])] = rec

    for idx, batch in mine.iter_batches(tickets, batch_size):
        if max_batches is not None and idx >= max_batches:
            break
        if idx in done:
            continue
        items, stats = mine.mine_batch(batch, chat=chat, term_df=term_df, params=params)
        rec = {"run_id": run_id, "batch": idx, "items": items, "stats": stats}
        done[idx] = rec
        with open(progress_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"batch {idx}: {stats}", file=sys.stderr)

    items_all: list[dict[str, Any]] = []
    keys: dict[str, dict[str, Any]] = {}
    stats_total: dict[str, int] = {}
    for idx in sorted(done):
        _merge_stats(stats_total, done[idx]["stats"])
        for it in done[idx]["items"]:
            k = "|".join(sorted(t.lower() for t in it["terms"]))
            if k in keys:  # same group from another batch: union the evidence tickets
                have = keys[k]["evidence"]["tickets"]
                have += [t for t in it["evidence"]["tickets"] if t not in have]
                continue
            keys[k] = it
            items_all.append(it)
    stats_total["batches_done"] = len(done)
    result = {"run_id": run_id, "model": model, "items": items_all, "stats": stats_total}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Synonym candidate mining")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mine")
    m.add_argument("--since-years", type=float, default=2.0)
    m.add_argument("--limit-tickets", type=int, default=None)
    m.add_argument("--batch-size", type=int, default=100)
    m.add_argument("--max-batches", type=int, default=None)
    m.add_argument("--run-id", default=None)
    m.add_argument("--min-terms-per-ticket", type=int, default=2, choices=[1, 2])
    m.add_argument("--df-min", type=int, default=3)
    m.add_argument("--df-max", type=int, default=1500)
    m.add_argument("--out", required=True)
    m.add_argument("--commit", action="store_true", help="also insert accepted candidates as 'proposed'")

    i = sub.add_parser("import")
    i.add_argument("--file", required=True)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    if args.cmd == "import":
        from app.synonyms import store

        data = json.load(open(args.file, encoding="utf-8"))
        res = store.upsert_candidates(data["items"], run_id=data.get("run_id"), model=data.get("model"))
        print(json.dumps(res))
        return 0

    from app.llm_chat import chat_complete
    from app.settings import get_settings
    from app.synonyms import store

    settings = get_settings()
    if settings.llm_backend == "none":
        print("LLM not configured", file=sys.stderr)
        return 2
    model = settings.company_model_id if settings.llm_backend == "fabrix" else settings.openrouter_model_id
    tickets = mine.load_tickets(since_years=args.since_years, limit=args.limit_tickets)
    print(f"tickets: {len(tickets)} backend={settings.llm_backend} model={model}", file=sys.stderr)
    params = mine.Params(
        df_min=args.df_min, df_max=args.df_max,
        min_terms_per_ticket=args.min_terms_per_ticket,
        existing_keys=store.existing_keys(),
    )
    result = run_mine(
        tickets=tickets, batch_size=args.batch_size, chat=chat_complete,
        term_df=mine.make_term_df(), params=params,
        run_id=args.run_id or uuid.uuid4().hex[:12], model=model,
        out_path=args.out, max_batches=args.max_batches,
    )
    print(json.dumps(result["stats"], ensure_ascii=False))
    if args.commit:
        print(json.dumps(store.upsert_candidates(result["items"], run_id=result["run_id"], model=model)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_synonyms_mine.py -q`
Expected: PASS

Run (개발 서버, 스크래치가 아닌 dev DB, 읽기 전용 확인): `python -m app.synonyms.cli mine --help`
Expected: 옵션 목록 출력, 에러 없음

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/synonyms/mine.py apps/api/app/synonyms/cli.py apps/api/tests/test_synonyms_mine.py
git commit -m "feat(synonyms): T6 ticket loader, FTS DF counter and resumable mine/import CLI

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 승인 API 라우터

**Files:**
- Create: `apps/api/app/routers/synonym_candidates.py`
- Modify: `apps/api/app/main.py` (lexicon 라우터 등록 바로 아래)
- Test: `apps/api/tests/test_synonyms_router.py`

- [ ] **Step 1: 실패하는 테스트 작성** (store를 monkeypatch, DB 불필요)

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.routers.synonym_candidates as r
from app.synonyms import runtime as rt, store


def _client():
    app = FastAPI()
    app.include_router(r.router)
    return TestClient(app)


def test_list_passes_filters(monkeypatch):
    seen = {}

    def fake(status=None, limit=100, offset=0):
        seen.update(status=status, limit=limit, offset=offset)
        return {"total": 0, "items": []}

    monkeypatch.setattr(store, "list_candidates", fake)
    res = _client().get("/v1/lexicon/synonym-candidates?status=proposed&limit=5")
    assert res.status_code == 200 and seen == {"status": "proposed", "limit": 5, "offset": 0}


def test_approve_resets_runtime_cache(monkeypatch):
    called = {}
    monkeypatch.setattr(store, "set_status", lambda cid, action, decided_by, note=None: {"id": cid, "status": "approved"})
    monkeypatch.setattr(rt, "reset_cache", lambda: called.setdefault("reset", True))
    res = _client().post("/v1/lexicon/synonym-candidates/7/approve", json={"note": "ok"})
    assert res.status_code == 200 and res.json()["status"] == "approved" and called["reset"]


def test_transition_and_not_found_errors(monkeypatch):
    def illegal(cid, action, decided_by, note=None):
        raise store.TransitionError("cannot disable a proposed candidate")

    def missing(cid, action, decided_by, note=None):
        raise store.NotFound("nope")

    monkeypatch.setattr(store, "set_status", illegal)
    assert _client().post("/v1/lexicon/synonym-candidates/1/disable", json={}).status_code == 409
    monkeypatch.setattr(store, "set_status", missing)
    assert _client().post("/v1/lexicon/synonym-candidates/1/approve", json={}).status_code == 404


def test_import_validates_and_forwards(monkeypatch):
    monkeypatch.setattr(store, "upsert_candidates", lambda items, run_id, model: {"inserted": len(items), "skipped": 0, "invalid": 0})
    body = {"run_id": "r", "model": "m", "items": [{"terms": ["OOM2x", "메모리 고갈"], "evidence": {}}]}
    res = _client().post("/v1/lexicon/synonym-candidates/import", json=body)
    assert res.status_code == 200 and res.json()["inserted"] == 1
    assert _client().post("/v1/lexicon/synonym-candidates/import", json={"items": "x"}).status_code == 422
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_synonyms_router.py -q`
Expected: FAIL — `ModuleNotFoundError: app.routers.synonym_candidates`

- [ ] **Step 3: 구현**

`apps/api/app/routers/synonym_candidates.py`:

```python
"""Synonym candidate review API (search-only; see docs/superpowers/specs/2026-11-02-…)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.synonyms import runtime, store

router = APIRouter(prefix="/v1/lexicon/synonym-candidates", tags=["lexicon"])

_WRITE_ROLES = ("senior", "admin")


class DecisionBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


class ImportItem(BaseModel):
    terms: list[str]
    evidence: dict[str, Any] = Field(default_factory=dict)


class ImportBody(BaseModel):
    run_id: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=128)
    items: list[ImportItem] = Field(max_length=2000)


@router.get("")
def list_candidates(
    status: Optional[str] = Query(None, pattern="^(proposed|approved|rejected|disabled)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return store.list_candidates(status=status, limit=limit, offset=offset)


@router.post("/import")
def import_candidates(
    body: ImportBody,
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, int]:
    return store.upsert_candidates(
        [i.model_dump() for i in body.items], run_id=body.run_id, model=body.model
    )


def _decide(cid: int, action: str, body: DecisionBody, principal: Principal) -> dict[str, Any]:
    try:
        row = store.set_status(cid, action, decided_by=principal.name, note=body.note)
    except store.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except store.TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    runtime.reset_cache()  # this instance sees the change at once; others after the TTL
    return row


@router.post("/{cid}/approve")
def approve(
    cid: int, body: DecisionBody,
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    return _decide(cid, "approve", body, principal)


@router.post("/{cid}/reject")
def reject(
    cid: int, body: DecisionBody,
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    return _decide(cid, "reject", body, principal)


@router.post("/{cid}/disable")
def disable(
    cid: int, body: DecisionBody,
    principal: Principal = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    return _decide(cid, "disable", body, principal)
```

`main.py`: import 블록(39행 부근 `from app.routers import lexicon as lexicon_router  # noqa: E402`) 아래에

```python
from app.routers import synonym_candidates as synonym_candidates_router  # noqa: E402
```

그리고 `app.include_router(lexicon_router.router)`(110행 부근) 아래에

```python
app.include_router(synonym_candidates_router.router)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_synonyms_router.py -q`
Expected: PASS

Run: `python -c "import app.main"` (apps/api에서)
Expected: 에러 없이 종료 (라우트 충돌 없음)

- [ ] **Step 5: 커밋**

```bash
git add apps/api/app/routers/synonym_candidates.py apps/api/app/main.py apps/api/tests/test_synonyms_router.py
git commit -m "feat(synonyms): T7 candidate review API (list/import/approve/reject/disable)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 지연 회귀 방지 확인 (ILIKE 제외 유지)

런타임 그룹이 늘어도 title ILIKE 스캔으로 새지 않아야 한다 (8ce9bf8의 교훈). `search.py`의 `no_like = {x.lower() for x in search_synonyms(base)}`가 이미 `search_synonyms`를 통하므로 DB 그룹도 자동 제외된다. 이를 테스트로 고정한다.

**Files:**
- Test: `apps/api/tests/test_synonyms_runtime.py` (추가)
- 변경 없음: `apps/api/app/retrieval/search.py`

- [ ] **Step 1: 테스트 추가**

먼저 `search.py` 660행 부근에서 `like_terms`/`no_like`를 계산하는 함수 이름을 확인한다.

Run: `grep -n "no_like" apps/api/app/retrieval/search.py`

해당 함수를 테스트에서 직접 호출할 수 없으면(세션이 필요한 경우), 아래처럼 variants 단계만 검증한다:

```python
def test_runtime_synonyms_reach_fts_variants_but_not_like_set(monkeypatch):
    from app.retrieval.search import build_fts_variants

    _reset(monkeypatch, lambda: [["inode2x", "아이노드 고갈"]])
    v = build_fts_variants("inode2x 확인", restricted=True)
    assert "아이노드 고갈" in v  # FTS variant present
    # LIKE exclusion is driven by search_synonyms(base); runtime members are included:
    assert "아이노드 고갈" in ss.search_synonyms("inode2x 확인")
    rt.reset_cache()
```

- [ ] **Step 2: 실행**

Run: `python -m pytest tests/test_synonyms_runtime.py -q`
Expected: PASS

- [ ] **Step 3: 커밋**

```bash
git add apps/api/tests/test_synonyms_runtime.py
git commit -m "test(synonyms): T8 pin that runtime groups feed FTS variants and the LIKE exclusion set

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: 래퍼 스크립트

**Files:**
- Create: `scripts/synonym_mine.sh`

- [ ] **Step 1: 작성**

```bash
#!/usr/bin/env bash
# api 컨테이너 안에서 python -m app.synonyms.cli 를 실행한다.
#   ./scripts/synonym_mine.sh mine --since-years 2 --out /tmp/syn-RUN.json
#   ./scripts/synonym_mine.sh import --file /tmp/syn-RUN.json
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICE="${SERVICE:-api}"
exec docker compose exec -T "$SERVICE" python -m app.synonyms.cli "$@"
```

- [ ] **Step 2: 확인**

Run: `chmod +x scripts/synonym_mine.sh && bash -n scripts/synonym_mine.sh && echo ok`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add scripts/synonym_mine.sh
git commit -m "feat(synonyms): T9 docker compose wrapper for the mining CLI

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 런북과 스펙 갱신

**Files:**
- Create: `docs/SYNONYM_MINING_RUNBOOK_20261102.md`
- Modify: `docs/superpowers/specs/2026-11-02-synonym-candidate-mining-design.md` (§4-1, §4-3 반영)

- [ ] **Step 1: 런북 작성** — 아래 내용 그대로.

````markdown
# 동의어 후보 마이닝 런북 (2026-11-02)

모든 운영 명령은 사용자가 실행한다. 후보는 검증을 통과한 것만 `proposed`로 저장되고, `approve` 전에는 검색에 영향이 없다.

## 0. 배포 (운영, 사용자)
```bash
./scripts/git_pull_deploy.sh      # alembic 0017 적용 포함. 완료 후 아래로 확인
docker compose exec -T api python -m alembic current   # 20261102_0017
```

## 1. 소규모 시험 (운영, dry-run — DB 쓰기 없음)
```bash
./scripts/synonym_mine.sh mine --since-years 2 --limit-tickets 300 --batch-size 100 \
  --run-id trial1 --out /tmp/syn-trial1.json
docker compose exec -T api cat /tmp/syn-trial1.json | head -80
```
확인할 것: `stats`의 `accepted` 수, `rej_*` 분포, `llm_error`, 후보 용어가 상식적인지.
후보가 거의 없고 `rej_evidence`가 크면 한 티켓에 두 표현이 같이 나오지 않는 것이다 → `--min-terms-per-ticket 1`로 재시도.

## 2. 전체 실행 (운영)
```bash
./scripts/synonym_mine.sh mine --since-years 2 --batch-size 100 --run-id full1 --out /tmp/syn-full1.json
docker compose cp api:/tmp/syn-full1.json /tmp/syn-full1.json
```
중단되면 같은 `--run-id`, `--out`으로 다시 실행하면 완료된 배치는 건너뛴다.

## 3. 개발 서버에서 게이트 검증 (개발, 어시스턴트와 함께)
1. `/tmp/syn-full1.json`을 개발 서버로 복사 후 개발 DB에 import:
   `./scripts/synonym_mine.sh import --file /tmp/syn-full1.json`
2. 개발 `.env`에 `SEARCH_SYNONYMS_INCLUDE_PROPOSED=1` 추가 → `docker compose up -d api`
3. 게이트 실행 (기준선은 `artifacts/citec-kb-retrieval-fix-20261028/after-latency-fix.json`):
   `NO_COLOR=1 python3 -u scripts/verify/eval_retrieval_gate.py run --base-url http://localhost:8573 --out artifacts/…/after-synonyms.json`
   `python3 scripts/verify/eval_retrieval_gate.py compare <baseline> <after-synonyms.json>`
   (정확한 인자는 `artifacts/citec-kb-retrieval-fix-20261028/README.md`의 명령을 따른다)
4. 통과 조건: holdout recall@10 후퇴 없음, pass→fail 없음, p50/p95 지연 증가 없음(별도 기록).
5. 후퇴가 있으면 후보를 절반씩 나눠 원인 그룹을 찾는다 (`reject`/`disable`).

## 4. 승인 (운영, 사용자)
```bash
curl -s "$BASE/v1/lexicon/synonym-candidates?status=proposed&limit=200" -H "X-API-Key: $KEY" | jq .
# 운영에 후보 적재: ./scripts/synonym_mine.sh import --file /tmp/syn-full1.json   (또는 mine --commit)
curl -s -X POST "$BASE/v1/lexicon/synonym-candidates/<id>/approve" -H "X-API-Key: $KEY" -H 'content-type: application/json' -d '{"note":"gate pass"}'
```
승인 후 캐시 TTL(기본 60초) 안에 반영된다. 문제가 생기면 즉시 `…/<id>/disable`.

## 5. 승인 후 확인 (운영)
- 승인 전후 같은 측정 명령으로 `/v1/search` p50/p95를 비교한다 (이전 측정과 동일 질의).
- 의심되는 그룹은 `disable`로 즉시 끈다.

## 6. 롤백
- 그룹 단위: `disable` API. 전체: `SEARCH_SYNONYMS_TTL_S` 기본값 그대로 두고 승인 그룹을 모두 disable.
- 스키마: `alembic downgrade 20261014_0016` (테이블 삭제, 코드 그룹은 영향 없음).
````

- [ ] **Step 2: 스펙 갱신** — 스펙 문서 §4 "3. 코드 검증 필터"의 근거 규칙 줄 다음에 `--min-terms-per-ticket`(기본 2, 완화 시 1) 설명을 추가하고, §4 "1. 결정적 선별"을 "DF 범위는 입력 선별이 아니라 검증 단계의 필터로 적용한다"로 고친다. ASCII 3자 미만 거부도 §4-3에 추가한다.

- [ ] **Step 3: 커밋**

```bash
git add docs/SYNONYM_MINING_RUNBOOK_20261102.md docs/superpowers/specs/2026-11-02-synonym-candidate-mining-design.md
git commit -m "docs(synonyms): T10 operation runbook and spec sync

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 전체 회귀

- [ ] **Step 1: 전체 단위 테스트**

Run: `python -m pytest tests -q -x --deselect tests/test_ops_dashboard 2>&1 | tail -15`
Expected: 실패 0 (백로그에 기록된 `test_ops_dashboard_*` 3건은 기존 실패이므로 제외 또는 동일 3건만 실패해야 한다). 새 실패가 있으면 해당 Task로 돌아가 수정한다.

- [ ] **Step 2: 스크래치 DB 테스트**

Run: `CONFLUENCE_SYNC_TEST_DATABASE_URL=<scratch dsn> python -m pytest tests/test_synonyms_store_db.py -q`
Expected: 4 passed

- [ ] **Step 3: 개발 서버 스모크** (읽기 전용 + 개발 DB)

```bash
cd /home/citec/dev/citec-kb
docker compose exec -T api python -m alembic upgrade head
docker compose restart api
curl -s "http://localhost:8573/v1/lexicon/synonym-candidates?limit=1"
```
Expected: `{"total":0,"items":[]}`

개발에서 Fabrix가 없으면(OpenRouter 사용) `mine --limit-tickets 100 --out /tmp/x.json`으로 파이프라인만 확인한다 (후보 품질은 운영 Fabrix에서 평가).

---

## Self-Review

**Spec coverage**

| 스펙 | Task |
|---|---|
| §3 테이블 | T2 |
| §4 마이너(배치, 검증 필터, 재개, 오류 격리) | T5, T6 |
| §4 한글 1~2자·DF·그룹 크기·중복·근거 티켓 | T1, T5 |
| §5 승인 API(list/import/approve/reject/disable), 권한 | T7 |
| §5 런타임 병합·캐시·DB 오류 폴백 | T4 |
| §5 ILIKE 제외 유지 | T8 |
| §6 게이트 검증 흐름·개발 전용 플래그 | T4(`SEARCH_SYNONYMS_INCLUDE_PROPOSED`), T10 |
| §7 비범위(자동 승인·그래프 변경 없음) | 해당 코드 없음 — `lexicon_terms`·`lexicon_version` 미접촉 |
| §8 테스트 | T1, T3, T4, T5, T6, T7, T8 |
| §10 미결(범위·DF 값) | T6 기본값 `--since-years 2`, `df 3..1500`, 런북 1단계에서 소규모 시험으로 확인 |

**Placeholder scan:** 없음. 단 T3·T11의 `<scratch dsn>`과 T10 런북의 `$BASE`, `$KEY`는 환경값 자리이며 `scripts/scratch_db_test.sh`와 운영 환경에서 확인한다.

**Type consistency:** `Ticket(external_id, title, symptom, text)`, `Params(df_min, df_max, min_terms_per_ticket, existing_keys)`, `process_groups(groups, tickets, *, term_df, params)`, `mine_batch(tickets, *, chat, term_df, params)`, `store.upsert_candidates(items, *, run_id, model)`, `store.set_status(cid, action, *, decided_by, note)`, `store.load_groups(*, include_proposed)`, `rt.runtime_groups()/reset_cache()` — 모든 Task에서 동일하게 쓰였다.

**알려진 위험**
- `min_terms_per_ticket=2` 기본값은 후보 수가 적을 수 있다 → 런북 1단계에서 확인 후 1로 완화.
- 개발 서버의 LLM은 OpenRouter라 후보 품질 평가는 운영 Fabrix 결과로 해야 한다.
- 게이트는 지연을 판정하지 않으므로 T10 3-4, 5단계에서 p50/p95를 직접 측정한다.
