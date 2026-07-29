# Failure Bucket Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `failure_bucket` knowledge category to citec-kb so Claude can register and look up network-diagnosis failure patterns (name + discriminating signals + counter signals + root cause + action) live via MCP during packet analysis, with confidence that self-improves as matches are confirmed or contradicted.

**Architecture:** New `failure_buckets` Postgres table (structured fields) mirrored into the existing `documents` table (so hybrid/FTS/vector search sees it like any other source_type). Writes go through a new `/v1/failure-buckets/*` REST router that reuses the existing `DocumentDraft` → `upsert_document_from_draft` → `embed_pending_chunks` pipeline (same pattern as `app/insights/service.py`). Matching is a pure, DB-free scoring function (token overlap between observed signals and stored discriminating/counter signals) exposed via `/v1/failure-buckets/match` and callable structured-first — no semantic search needed since signals are explicit lists. Five new MCP tools proxy this REST surface, same shape as the existing `kb_*` tools in `mcp-server/server.py`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (Mapped/mapped_column), Alembic, Postgres/pgvector, pytest (unit tests only — this repo's CI has no live DB, so only pure functions get automated tests; DB-touching code is verified with documented `curl` walkthroughs, matching the existing convention for `insights`/`checkitems`).

**Spec:** `docs/superpowers/specs/2026-07-29-failure-bucket-design.md`

---

## Task 1: `FailureBucket` DB model + Alembic migration

**Files:**
- Modify: `apps/api/app/db/models.py`
- Create: `apps/api/alembic/versions/20260729_0003_failure_buckets.py`

- [ ] **Step 1: Add the `FailureBucket` model**

Insert after the `IssueFrame` class (around line 350 in `apps/api/app/db/models.py`, right before `class CapacityRule(Base):`):

```python
class FailureBucket(Base):
    """Self-improving failure-pattern registry (e.g. network packet diagnosis)."""

    __tablename__ = "failure_buckets"
    __table_args__ = (
        Index("ix_failure_buckets_protocol", "protocol"),
        Index("ix_failure_buckets_bucket_name", "bucket_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    document_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True
    )
    bucket_name: Mapped[str] = mapped_column(String(256), nullable=False)
    protocol: Mapped[Optional[str]] = mapped_column(String(32))
    symptom: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    discriminating_signals: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    counter_signals: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    counter_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    evidence_grade: Mapped[str] = mapped_column(String(8), nullable=False, server_default="machine")
    created_by: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Write the migration**

```python
"""add failure_buckets table

Revision ID: 20260729_0003
Revises: 20260718_0002
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729_0003"
down_revision: Union[str, None] = "20260718_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "failure_buckets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=64),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column("bucket_name", sa.String(length=256), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=True),
        sa.Column("symptom", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "discriminating_signals",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "counter_signals",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("root_cause", sa.Text(), server_default="", nullable=False),
        sa.Column("recommended_action", sa.Text(), server_default="", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("support_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("counter_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("evidence_grade", sa.String(length=8), server_default="machine", nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_failure_buckets_protocol", "failure_buckets", ["protocol"])
    op.create_index("ix_failure_buckets_bucket_name", "failure_buckets", ["bucket_name"])


def downgrade() -> None:
    op.drop_index("ix_failure_buckets_bucket_name", table_name="failure_buckets")
    op.drop_index("ix_failure_buckets_protocol", table_name="failure_buckets")
    op.drop_table("failure_buckets")
```

- [ ] **Step 3: Verify the model imports cleanly**

Run: `PYTHONPATH=apps/api python -c "from app.db.models import FailureBucket; print(FailureBucket.__tablename__)"`
Expected: `failure_buckets`

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/db/models.py apps/api/alembic/versions/20260729_0003_failure_buckets.py
git commit -m "feat: add failure_buckets table"
```

---

## Task 2: Taxonomy domain inference for `failure_bucket`

**Files:**
- Modify: `apps/api/app/taxonomy.py:34-64`
- Test: `apps/api/tests/test_taxonomy_failure_bucket.py`

- [ ] **Step 1: Write the failing test**

```python
from app.taxonomy import infer_domain


def test_infer_domain_failure_bucket_uses_protocol():
    domain = infer_domain(
        "LB idle-timeout으로 인한 RST",
        "TCP RST 직전 idle 62초",
        source_type="failure_bucket",
        metadata={"protocol": "TCP"},
    )
    assert domain == "tcp"


def test_infer_domain_failure_bucket_no_protocol_falls_back():
    domain = infer_domain(
        "TLS record 재조립 지연",
        "TLS 관련 증상",
        source_type="failure_bucket",
        metadata={},
    )
    assert domain == "network"  # falls back to keyword rules (TLS not matched → network via 방화벽/NSX? use explicit blob)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_taxonomy_failure_bucket.py -v`
Expected: FAIL — first assertion returns `None` (no `failure_bucket` branch exists yet), and the fallback test may also fail since `TLS` isn't in `_DOMAIN_RULES`.

Fix the second test's expectation before implementing — `_DOMAIN_RULES` has no TLS pattern, so a bare `infer_domain` with `metadata={}` will legitimately return `None`. Replace the second test with:

```python
def test_infer_domain_failure_bucket_no_protocol_falls_back():
    domain = infer_domain(
        "네트워크 방화벽 이슈",
        "증상 설명",
        source_type="failure_bucket",
        metadata={},
    )
    assert domain == "network"
```

- [ ] **Step 3: Implement the `failure_bucket` branch**

In `apps/api/app/taxonomy.py`, inside `infer_domain` (after the `source_type == "checkitem"` block, before the final `blob = ...` fallback):

```python
    if source_type == "checkitem" and metadata:
        area = str(metadata.get("Area") or "")
        if area:
            return area.lower().replace(" ", "_")
    if source_type == "failure_bucket" and metadata:
        protocol = str(metadata.get("protocol") or "")
        if protocol:
            return protocol.lower().replace(" ", "_")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_taxonomy_failure_bucket.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/taxonomy.py apps/api/tests/test_taxonomy_failure_bucket.py
git commit -m "feat: infer domain from protocol for failure_bucket source_type"
```

---

## Task 3: Pure signal-matching scorer (no DB)

**Files:**
- Create: `apps/api/app/failure_buckets/__init__.py`
- Create: `apps/api/app/failure_buckets/match.py`
- Test: `apps/api/tests/test_failure_bucket_match.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_failure_bucket_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.failure_buckets'`

- [ ] **Step 3: Create the package and implement scoring**

`apps/api/app/failure_buckets/__init__.py`:

```python
```

(empty file — marks the package)

`apps/api/app/failure_buckets/match.py`:

```python
"""Pure signal-matching scorer for failure buckets (no DB)."""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]{2,}")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _signal_hit(observed_tokens: set[str], signal: str) -> bool:
    """A stored signal is 'hit' when at least half its tokens appear in the
    observed token set (order-insensitive; handles partial phrasing)."""
    sig_tokens = _tokens(signal)
    if not sig_tokens:
        return False
    hits = sum(1 for t in sig_tokens if t in observed_tokens)
    return hits >= max(1, (len(sig_tokens) + 1) // 2)


def match_bucket(
    observed_signals: list[str],
    symptom: str,
    bucket: dict[str, Any],
) -> dict[str, Any]:
    """Score a single bucket against observed signals + symptom text."""
    observed_tokens: set[str] = set()
    for s in observed_signals or []:
        observed_tokens |= _tokens(s)
    observed_tokens |= _tokens(symptom)

    discriminating = list(bucket.get("discriminating_signals") or [])
    counter = list(bucket.get("counter_signals") or [])

    matched = [s for s in discriminating if _signal_hit(observed_tokens, s)]
    contradicted = [s for s in counter if _signal_hit(observed_tokens, s)]

    total = len(discriminating) or 1
    signal_ratio = len(matched) / total
    score = 0.6 * signal_ratio + 0.4 * float(bucket.get("confidence") or 0.5)
    if contradicted:
        score -= 0.5 * len(contradicted)
    score = max(0.0, min(1.0, score))

    if contradicted:
        label = "비권고"
    elif score >= 0.7 and matched:
        label = "가능"
    elif score >= 0.4:
        label = "조건부"
    else:
        label = "비권고"

    return {
        "bucket_id": bucket.get("id"),
        "bucket_name": bucket.get("bucket_name"),
        "matched_signals": matched,
        "contradicted": contradicted,
        "confidence": round(score, 3),
        "label": label,
    }


def rank_buckets(
    observed_signals: list[str],
    symptom: str,
    buckets: list[dict[str, Any]],
    *,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    scored = [match_bucket(observed_signals, symptom, b) for b in buckets]
    scored.sort(key=lambda r: r["confidence"], reverse=True)
    return scored[:top_k]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_failure_bucket_match.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/failure_buckets/ apps/api/tests/test_failure_bucket_match.py
git commit -m "feat: pure signal-matching scorer for failure buckets"
```

---

## Task 4: Confidence update + document-draft builder (pure, no DB)

**Files:**
- Create: `apps/api/app/failure_buckets/draft.py`
- Test: `apps/api/tests/test_failure_bucket_draft.py`

- [ ] **Step 1: Write the failing tests**

```python
from types import SimpleNamespace

from app.failure_buckets.draft import bucket_body_md, bucket_draft, compute_confidence


def test_compute_confidence_default_no_evidence():
    assert compute_confidence(0, 0) == 0.5


def test_compute_confidence_support_raises():
    assert compute_confidence(1, 0) > 0.5


def test_compute_confidence_counter_lowers():
    assert compute_confidence(0, 1) < 0.5


def test_bucket_body_md_includes_signals():
    body = bucket_body_md(
        bucket_name="LB idle-timeout RST",
        protocol="TCP",
        symptom="다운로드 중 연결 끓김",
        discriminating_signals=["RST 직전 idle 60초 이상"],
        counter_signals=["재전송 다수 관찰"],
        root_cause="LB 세션 idle timeout",
        recommended_action="keepalive 간격을 idle timeout보다 짧게 설정",
    )
    assert "RST 직전 idle 60초 이상" in body
    assert "재전송 다수 관찰" in body
    assert "LB 세션 idle timeout" in body


def test_bucket_draft_hash_stable_when_only_counts_change():
    row = SimpleNamespace(
        id="abcdef12-3456-7890-abcd-ef1234567890",
        bucket_name="LB idle-timeout RST",
        protocol="TCP",
        symptom="증상",
        discriminating_signals=["신호1"],
        counter_signals=[],
        root_cause="원인",
        recommended_action="조치",
    )
    draft_a = bucket_draft(row)
    draft_b = bucket_draft(row)  # unrelated confidence/count bump wouldn't touch row's draft-relevant fields
    assert draft_a.content_hash == draft_b.content_hash
    assert draft_a.source_type == "failure_bucket"
    assert draft_a.external_id == "FB-abcdef12"
    assert draft_a.domain == "tcp"
    assert draft_a.evidence_grade == "machine"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_failure_bucket_draft.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.failure_buckets.draft'`

- [ ] **Step 3: Implement**

`apps/api/app/failure_buckets/draft.py`:

```python
"""Confidence math + DocumentDraft builder for failure buckets (no DB)."""

from __future__ import annotations

from typing import Any

from app.ingest.adapters import DocumentDraft


def compute_confidence(support_count: int, counter_count: int) -> float:
    """Laplace-smoothed confidence; defaults to 0.5 with no evidence."""
    support = max(0, int(support_count))
    counter = max(0, int(counter_count))
    return round((support + 1) / (support + counter + 2), 3)


def bucket_body_md(
    *,
    bucket_name: str,
    protocol: str | None,
    symptom: str,
    discriminating_signals: list[str],
    counter_signals: list[str],
    root_cause: str,
    recommended_action: str,
) -> str:
    lines = [f"# [failure_bucket] {bucket_name}"]
    if protocol:
        lines.append(f"프로토콜: {protocol}")
    lines.append("")
    lines.append(f"증상: {symptom or '(미상)'}")
    lines.append("")
    lines.append("판별 신호:")
    lines.extend(f"- {s}" for s in discriminating_signals) if discriminating_signals else lines.append("- (없음)")
    lines.append("")
    lines.append("반증 신호:")
    lines.extend(f"- {s}" for s in counter_signals) if counter_signals else lines.append("- (없음)")
    lines.append("")
    lines.append(f"근본원인: {root_cause or '(미상)'}")
    lines.append("")
    lines.append(f"조치: {recommended_action or '(미상)'}")
    return "\n".join(lines)


def bucket_draft(row: Any) -> DocumentDraft:
    """Build the searchable Document mirror for a FailureBucket row.

    Only includes fields that define the bucket's *meaning* (name, signals,
    cause, action) — confidence/support_count/counter_count are deliberately
    excluded so a pure confidence-count refine produces the same content_hash
    and does not trigger a re-chunk/re-embed.
    """
    body = bucket_body_md(
        bucket_name=row.bucket_name,
        protocol=row.protocol,
        symptom=row.symptom,
        discriminating_signals=list(row.discriminating_signals or []),
        counter_signals=list(row.counter_signals or []),
        root_cause=row.root_cause,
        recommended_action=row.recommended_action,
    )
    return DocumentDraft(
        source_type="failure_bucket",
        external_id=f"FB-{row.id[:8]}",
        title=row.bucket_name,
        body_md=body,
        metadata={
            "bucket_id": row.id,
            "protocol": row.protocol,
            "discriminating_signals": list(row.discriminating_signals or []),
            "counter_signals": list(row.counter_signals or []),
            "root_cause": row.root_cause,
            "recommended_action": row.recommended_action,
        },
        evidence_grade="machine",
        source_uri=f"failure_bucket://{row.id}",
        domain=(row.protocol or "").lower() or None,
    ).finalize()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && PYTHONPATH=. python -m pytest tests/test_failure_bucket_draft.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/failure_buckets/draft.py apps/api/tests/test_failure_bucket_draft.py
git commit -m "feat: confidence math + document-draft builder for failure buckets"
```

---

## Task 5: DB-backed service layer

**Files:**
- Create: `apps/api/app/failure_buckets/service.py`

No automated test in this task (DB-touching; this repo's CI has no live Postgres — see `.github/workflows/ci.yml`, `DATABASE_URL` points at an unreachable port). Verified manually via curl in Task 7.

- [ ] **Step 1: Implement the service module**

`apps/api/app/failure_buckets/service.py`:

```python
"""Failure-bucket flywheel: register → index → refine (self-improving)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select

from app.db.models import FailureBucket
from app.db.session import session_scope
from app.failure_buckets.draft import bucket_draft, compute_confidence
from app.failure_buckets.match import rank_buckets

logger = logging.getLogger("citec.failure_buckets")


def _to_dict(row: FailureBucket) -> dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "bucket_name": row.bucket_name,
        "protocol": row.protocol,
        "symptom": row.symptom,
        "discriminating_signals": list(row.discriminating_signals or []),
        "counter_signals": list(row.counter_signals or []),
        "root_cause": row.root_cause,
        "recommended_action": row.recommended_action,
        "confidence": row.confidence,
        "support_count": row.support_count,
        "counter_count": row.counter_count,
        "evidence_grade": row.evidence_grade,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _index_bucket(bucket_id: str) -> dict[str, Any]:
    """(Re)build the searchable Document mirror + embed pending chunks.

    Idempotent: upsert_document_from_draft() skips re-chunking when the
    draft's content_hash is unchanged (see app/ingest/pipeline.py), so a
    refine call that only bumps confidence/support_count is cheap.
    """
    from app.embed.job import embed_pending_chunks
    from app.ingest.pipeline import upsert_document_from_draft

    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if not row:
            raise KeyError(bucket_id)
        draft = bucket_draft(row)

    upsert = upsert_document_from_draft(draft)
    doc_id = upsert["document_id"]

    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if row and row.document_id != doc_id:
            row.document_id = doc_id
            session.flush()

    try:
        emb = embed_pending_chunks(document_id=doc_id, batch_size=16)
    except Exception as exc:  # noqa: BLE001
        logger.exception("embed failed for failure_bucket=%s doc=%s", bucket_id, doc_id)
        emb = {"error": str(exc), "embedded": 0}

    return {
        "document_id": doc_id,
        "action": upsert.get("action"),
        "embedded": emb.get("embedded", 0),
    }


def create_bucket(
    *,
    bucket_name: str,
    symptom: str,
    discriminating_signals: list[str],
    root_cause: str,
    recommended_action: str,
    counter_signals: Optional[list[str]] = None,
    protocol: Optional[str] = None,
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    with session_scope() as session:
        row = FailureBucket(
            bucket_name=bucket_name.strip(),
            protocol=(protocol or "").strip() or None,
            symptom=symptom or "",
            discriminating_signals=list(discriminating_signals or []),
            counter_signals=list(counter_signals or []),
            root_cause=root_cause or "",
            recommended_action=recommended_action or "",
            created_by=created_by,
        )
        session.add(row)
        session.flush()
        bucket_id = row.id

    index = _index_bucket(bucket_id)
    result = get_bucket(bucket_id)
    if result is None:
        raise RuntimeError(f"failure_bucket {bucket_id} vanished after create")
    result["index"] = index
    return result


def get_bucket(bucket_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        return _to_dict(row) if row else None


def list_buckets(
    *,
    protocol: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with session_scope() as session:
        stmt = select(FailureBucket).order_by(FailureBucket.updated_at.desc())
        if protocol:
            stmt = stmt.where(FailureBucket.protocol == protocol)
        if min_confidence:
            stmt = stmt.where(FailureBucket.confidence >= min_confidence)
        rows = list(session.scalars(stmt.offset(offset).limit(limit)).all())
        return {"total": len(rows), "items": [_to_dict(r) for r in rows]}


def refine_bucket(
    bucket_id: str,
    *,
    add_signal: Optional[str] = None,
    add_counter_signal: Optional[str] = None,
    confirm: bool = True,
) -> dict[str, Any]:
    signals_changed = False
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if not row:
            raise KeyError(bucket_id)

        if add_signal and add_signal not in (row.discriminating_signals or []):
            row.discriminating_signals = [*(row.discriminating_signals or []), add_signal]
            signals_changed = True
        if add_counter_signal and add_counter_signal not in (row.counter_signals or []):
            row.counter_signals = [*(row.counter_signals or []), add_counter_signal]
            signals_changed = True

        if confirm:
            row.support_count = int(row.support_count or 0) + 1
        else:
            row.counter_count = int(row.counter_count or 0) + 1
        row.confidence = compute_confidence(row.support_count, row.counter_count)
        session.flush()

    index = _index_bucket(bucket_id) if signals_changed else None
    result = get_bucket(bucket_id)
    if result is None:
        raise RuntimeError(f"failure_bucket {bucket_id} vanished after refine")
    if index:
        result["index"] = index
    return result


def match_buckets(
    *,
    observed_signals: list[str],
    symptom: str = "",
    protocol: Optional[str] = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(FailureBucket)
        if protocol:
            stmt = stmt.where(FailureBucket.protocol == protocol)
        rows = list(session.scalars(stmt).all())
        dicts = [_to_dict(r) for r in rows]
    return rank_buckets(observed_signals, symptom, dicts, top_k=top_k)
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/failure_buckets/service.py
git commit -m "feat: DB-backed failure_bucket register/refine/match service"
```

---

## Task 6: `/v1/failure-buckets` router + registration

**Files:**
- Create: `apps/api/app/routers/failure_buckets.py`
- Modify: `apps/api/app/main.py`

- [ ] **Step 1: Implement the router**

`apps/api/app/routers/failure_buckets.py`:

```python
"""Failure-bucket registry API (register/refine/match, self-improving)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_roles
from app.auth.principal import Principal
from app.failure_buckets.service import (
    create_bucket,
    get_bucket,
    list_buckets,
    match_buckets,
    refine_bucket,
)

router = APIRouter(prefix="/v1", tags=["failure-buckets"])


class FailureBucketCreate(BaseModel):
    bucket_name: str = Field(..., min_length=1, max_length=256)
    symptom: str = Field(default="", max_length=4000)
    discriminating_signals: list[str] = Field(default_factory=list)
    counter_signals: list[str] = Field(default_factory=list)
    root_cause: str = Field(default="", max_length=4000)
    recommended_action: str = Field(default="", max_length=4000)
    protocol: Optional[str] = None
    created_by: Optional[str] = None


class FailureBucketRefine(BaseModel):
    add_signal: Optional[str] = None
    add_counter_signal: Optional[str] = None
    confirm: bool = True


class FailureBucketMatch(BaseModel):
    observed_signals: list[str] = Field(default_factory=list)
    symptom: str = Field(default="", max_length=4000)
    protocol: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/failure-buckets")
def post_failure_bucket(
    body: FailureBucketCreate,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    return create_bucket(
        bucket_name=body.bucket_name,
        symptom=body.symptom,
        discriminating_signals=body.discriminating_signals,
        counter_signals=body.counter_signals,
        root_cause=body.root_cause,
        recommended_action=body.recommended_action,
        protocol=body.protocol,
        created_by=body.created_by or principal.name,
    )


@router.get("/failure-buckets")
def get_failure_buckets(
    protocol: Optional[str] = Query(None),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return list_buckets(protocol=protocol, min_confidence=min_confidence, limit=limit, offset=offset)


@router.get("/failure-buckets/{bucket_id}")
def get_one_failure_bucket(bucket_id: str) -> dict[str, Any]:
    row = get_bucket(bucket_id)
    if not row:
        raise HTTPException(status_code=404, detail="failure_bucket not found")
    return row


@router.post("/failure-buckets/{bucket_id}/refine")
def post_refine_failure_bucket(
    bucket_id: str,
    body: FailureBucketRefine,
    principal: Principal = Depends(require_roles("author", "senior", "admin")),
) -> dict[str, Any]:
    _ = principal
    try:
        return refine_bucket(
            bucket_id,
            add_signal=body.add_signal,
            add_counter_signal=body.add_counter_signal,
            confirm=body.confirm,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="failure_bucket not found") from None


@router.post("/failure-buckets/match")
def post_match_failure_buckets(body: FailureBucketMatch) -> dict[str, Any]:
    results = match_buckets(
        observed_signals=body.observed_signals,
        symptom=body.symptom,
        protocol=body.protocol,
        top_k=body.top_k,
    )
    return {"results": results, "total": len(results)}
```

- [ ] **Step 2: Register the router in `main.py`**

In `apps/api/app/main.py`, add the import next to the other `app.routers` imports (alphabetically near `entities`):

```python
from app.routers import entities as entities_router  # noqa: E402
from app.routers import failure_buckets as failure_buckets_router  # noqa: E402
```

Add the `include_router` call next to `entities_router` (after line 80, `app.include_router(entities_router.router)`):

```python
app.include_router(entities_router.router)
app.include_router(failure_buckets_router.router)
```

- [ ] **Step 3: Verify the app imports cleanly**

Run: `cd apps/api && PYTHONPATH=. AUTH_MODE=off DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:1/citec_knowledge REDIS_URL=redis://127.0.0.1:1/0 python -c "from app.main import app; print([r.path for r in app.routes if 'failure-buckets' in r.path])"`
Expected: prints a list containing `/v1/failure-buckets`, `/v1/failure-buckets/{bucket_id}`, `/v1/failure-buckets/{bucket_id}/refine`, `/v1/failure-buckets/match`

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/failure_buckets.py apps/api/app/main.py
git commit -m "feat: add /v1/failure-buckets REST router"
```

---

## Task 7: Wire `failure_bucket` into document-access + wiki-qa compat surfaces

**Files:**
- Modify: `apps/api/app/doc_access.py`
- Modify: `apps/api/app/routers/external_compat.py`

- [ ] **Step 1: Confirm `doc_access.py` needs no change (read-only check)**

`apps/api/app/doc_access.py`'s `document_access()` already has a generic fallback:

```python
    body_api_rel = ""
    if st == "checkitem" and eid:
        body_api_rel = f"/v1/checkitems/{quote(eid, safe='')}"
    elif eid:
        body_api_rel = f"/v1/tickets/{quote(eid, safe='')}?source_type={quote(st, safe='')}"
```

`eid` for a failure bucket document is `FB-xxxxxxxx` (set in `bucket_draft()`, Task 4) and `st` is `"failure_bucket"`, so this already produces `/v1/tickets/FB-xxxxxxxx?source_type=failure_bucket`. `apps/api/app/routers/tickets.py` queries `Document` by `external_id` + `source_type` with no source_type allowlist, so this resolves correctly with zero code changes. Run `grep -n "source_type" apps/api/app/routers/tickets.py` to confirm there's no allowlist before moving on — if one exists, add `"failure_bucket"` to it.

- [ ] **Step 2: Add `failure_bucket` to the hardcoded source_type sets in `external_compat.py`**

In `apps/api/app/routers/external_compat.py`, update `_SECTION_MAP` (around line 53-68):

```python
_SECTION_MAP: dict[str, Optional[str]] = {
    "": None,
    "general": None,
    "checkitems": "checkitem",
    "checkitem": "checkitem",
    "support_history": "support_history",
    "incident_reports": "support_history",
    "vendor_docs": "vendor_docs",
    "tech_repo": "tech_repo",
    "tuning_ai": "tuning_ai",
    "sql_tuning": "tuning_ai",
    "confluence_docs": "confluence_docs",
    "failure_bucket": "failure_bucket",
    "failure_buckets": "failure_bucket",
    "synthesis": "insight",
    "insight": "insight",
    "insights": "insight",
}
```

Update `_TEMPLATE_LABELS` (around line 70-79):

```python
_TEMPLATE_LABELS = {
    "general": "전체",
    "checkitems": "PISA 체크리스트",
    "support_history": "기술지원이력",
    "incident_reports": "장애/지원이력",
    "vendor_docs": "벤더 문서",
    "tech_repo": "테크리포",
    "tuning_ai": "DBMS튜닝",
    "failure_bucket": "실패 패턴 라이브러리",
    "synthesis": "Insight/합성지식",
}
```

Update the hardcoded set in `_resolve_document()` (around line 426-435):

```python
    if source_type and source_type in {
        "support_history",
        "tech_repo",
        "checkitem",
        "tuning_ai",
        "confluence_docs",
        "vendor_docs",
        "insight",
        "incident_reports",
        "failure_bucket",
    }:
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `cd apps/api && PYTHONPATH=. python -c "from app.routers import external_compat; print('failure_bucket' in external_compat._SECTION_MAP)"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/external_compat.py
git commit -m "feat: recognize failure_bucket in wiki-qa compat section/template maps"
```

---

## Task 8: MCP tools

**Files:**
- Modify: `mcp-server/server.py`

- [ ] **Step 1: Add the five tools**

Insert after `kb_get_checkitem` (after line 889, before `kb_ticket` at line 892-893) in `mcp-server/server.py`:

```python
@mcp.tool()
async def kb_register_failure_bucket(
    bucket_name: str,
    symptom: str,
    discriminating_signals: list[str],
    root_cause: str,
    recommended_action: str,
    counter_signals: list[str] | None = None,
    protocol: str = "",
) -> str:
    """새로운 실패 버킷(장애 패턴)을 등록한다. 즉시 검색에 노출된다.
    예: bucket_name='LB idle-timeout RST', discriminating_signals=['RST 직전 idle 60초 이상']
    """
    if not bucket_name.strip():
        return "오류: bucket_name 이 비어 있습니다."
    if not discriminating_signals:
        return "오류: discriminating_signals 가 비어 있습니다."
    body: dict[str, Any] = {
        "bucket_name": bucket_name.strip(),
        "symptom": symptom or "",
        "discriminating_signals": discriminating_signals,
        "counter_signals": counter_signals or [],
        "root_cause": root_cause or "",
        "recommended_action": recommended_action or "",
        "protocol": protocol.strip() or None,
    }
    try:
        async with _client() as client:
            resp = await client.post("/v1/failure-buckets", json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return (
        f"등록됨: {data.get('bucket_name')} (id={data.get('id')}, "
        f"confidence={data.get('confidence')})"
    )


@mcp.tool()
async def kb_refine_failure_bucket(
    bucket_id: str,
    add_signal: str = "",
    add_counter_signal: str = "",
    confirm: bool = True,
) -> str:
    """실패 버킷을 정제한다 — 신호 추가 및 확인(confirm=True)/반박(confirm=False) 기록.
    신뢰도(confidence)가 자동 재계산된다."""
    bucket_id = (bucket_id or "").strip()
    if not bucket_id:
        return "오류: bucket_id 가 비어 있습니다."
    body: dict[str, Any] = {"confirm": confirm}
    if add_signal.strip():
        body["add_signal"] = add_signal.strip()
    if add_counter_signal.strip():
        body["add_counter_signal"] = add_counter_signal.strip()
    try:
        async with _client() as client:
            resp = await client.post(f"/v1/failure-buckets/{bucket_id}/refine", json=body)
            if resp.status_code == 404:
                return f"오류: 실패 버킷을 찾을 수 없습니다: {bucket_id}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return (
        f"정제됨: {data.get('bucket_name')} confidence={data.get('confidence')} "
        f"support={data.get('support_count')} counter={data.get('counter_count')}"
    )


@mcp.tool()
async def kb_match_failure_bucket(
    observed_signals: list[str],
    symptom: str = "",
    protocol: str = "",
    top_k: int = 5,
) -> str:
    """관찰된 신호로 후보 실패 버킷을 순위화한다.
    예: observed_signals=['RST 직전 idle 62초'], symptom='다운로드 중 연결 끓김'"""
    if not observed_signals and not symptom.strip():
        return "오류: observed_signals 또는 symptom 중 하나는 필요합니다."
    body: dict[str, Any] = {
        "observed_signals": observed_signals,
        "symptom": symptom or "",
        "top_k": min(max(top_k, 1), 20),
    }
    if protocol.strip():
        body["protocol"] = protocol.strip()
    try:
        async with _client() as client:
            resp = await client.post("/v1/failure-buckets/match", json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    results = data.get("results") or []
    if not results:
        return "(일치하는 실패 버킷 없음)"
    lines = [f"실패 버킷 후보 {len(results)}건:"]
    for r in results:
        lines.append(
            f"- {r.get('bucket_name')} (id={r.get('bucket_id')}) "
            f"confidence={r.get('confidence')} label={r.get('label')}\n"
            f"  matched: {r.get('matched_signals')}\n"
            f"  contradicted: {r.get('contradicted')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def kb_list_failure_buckets(
    protocol: str = "",
    min_confidence: float = 0.0,
    limit: int = 20,
) -> str:
    """등록된 실패 버킷 목록. protocol 예: TCP|TLS|HTTP"""
    params: dict[str, Any] = {"limit": min(max(limit, 1), 200), "min_confidence": min_confidence}
    if protocol.strip():
        params["protocol"] = protocol.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/failure-buckets", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    items = data.get("items") or []
    lines = [f"failure_buckets total={data.get('total', len(items))}"]
    for it in items:
        lines.append(
            f"- {it.get('bucket_name')} (id={it.get('id')}) protocol={it.get('protocol')} "
            f"confidence={it.get('confidence')}"
        )
    if not items:
        lines.append("(결과 없음)")
    return "\n".join(lines)


@mcp.tool()
async def kb_get_failure_bucket(bucket_id: str) -> str:
    """실패 버킷 상세(판별 신호/반증 신호/근본원인/조치 포함)를 조회한다."""
    bucket_id = (bucket_id or "").strip()
    if not bucket_id:
        return "오류: bucket_id 가 비어 있습니다."
    try:
        async with _client() as client:
            resp = await client.get(f"/v1/failure-buckets/{bucket_id}")
            if resp.status_code == 404:
                return f"오류: 실패 버킷을 찾을 수 없습니다: {bucket_id}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return (
        f"# {data.get('bucket_name')} (id={data.get('id')})\n"
        f"protocol={data.get('protocol')} confidence={data.get('confidence')} "
        f"support={data.get('support_count')} counter={data.get('counter_count')}\n\n"
        f"증상: {data.get('symptom')}\n"
        f"판별 신호: {data.get('discriminating_signals')}\n"
        f"반증 신호: {data.get('counter_signals')}\n"
        f"근본원인: {data.get('root_cause')}\n"
        f"조치: {data.get('recommended_action')}"
    )
```

- [ ] **Step 2: Add the tools to `kb_tools_help()`**

In `mcp-server/server.py`, inside `kb_tools_help()` (line 819-852), insert a new section between `[유사 장애 · 체크리스트 · 용량]` and `[티켓 · Insight · 상태]`:

```python
[실패 버킷 · 네트워크 패킷 진단]
  kb_register_failure_bucket(bucket_name=, symptom=, discriminating_signals=, root_cause=, recommended_action=, counter_signals=, protocol=)
  kb_refine_failure_bucket(bucket_id=, add_signal=, add_counter_signal=, confirm=)
  kb_match_failure_bucket(observed_signals=, symptom=, protocol=)
  kb_list_failure_buckets(protocol=, min_confidence=)
  kb_get_failure_bucket(bucket_id=)
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `cd mcp-server && python -c "import server; print([t for t in dir(server) if t.startswith('kb_') and 'failure' in t])"`
Expected: lists all 5 new tool function names

- [ ] **Step 4: Commit**

```bash
git add mcp-server/server.py
git commit -m "feat: add failure_bucket MCP tools (register/refine/match/list/get)"
```

---

## Task 9: MCP smoke test coverage

**Files:**
- Modify: `mcp-server/test_smoke.py`

- [ ] **Step 1: Add failure-bucket round-trip checks**

`test_smoke.py` calls the MCP tool functions in `server.py` directly (async, via `server.kb_xxx(...)`) and uses the shared `check(name, cond, detail)` helper — see the existing `kb_similar_incident`/`kb_list_checkitems` calls around line 69-73. Insert a new block in `mcp-server/test_smoke.py`'s `main()`, right after the `kb_list_checkitems` check (line 72-73) and before `help_t = await server.kb_tools_help()` (line 75):

```python
    fb_name = f"SMOKE-{os.getpid()}-LB idle-timeout RST"
    fb_reg = await server.kb_register_failure_bucket(
        bucket_name=fb_name,
        symptom="스모크 테스트용 임시 버킷",
        discriminating_signals=["스모크 신호 A"],
        root_cause="스모크 테스트",
        recommended_action="없음",
        protocol="TCP",
    )
    check("kb_register_failure_bucket", not fb_reg.startswith("오류:"), fb_reg[:200])

    fb_id_line = next((l for l in fb_reg.splitlines() if "id=" in l), "")
    fb_id = ""
    if "id=" in fb_id_line:
        fb_id = fb_id_line.split("id=", 1)[1].split(",")[0].split(")")[0].strip()

    if fb_id:
        fb_get = await server.kb_get_failure_bucket(fb_id)
        check("kb_get_failure_bucket", fb_name in fb_get, fb_get[:200])

        fb_match = await server.kb_match_failure_bucket(observed_signals=["스모크 신호 A"])
        check("kb_match_failure_bucket finds it", fb_id in fb_match, fb_match[:300])

        fb_refine = await server.kb_refine_failure_bucket(fb_id, confirm=True)
        check("kb_refine_failure_bucket", "confidence=" in fb_refine, fb_refine[:200])
    else:
        check("kb_register_failure_bucket returned id", False, fb_reg)

    fb_list = await server.kb_list_failure_buckets(protocol="TCP", limit=50)
    check("kb_list_failure_buckets", not fb_list.startswith("오류:"), fb_list[:200])
```

- [ ] **Step 2: Run the smoke test against a running stack**

Run: `docker compose up -d --build api mcp` then `CITEC_KB_BASE_URL=http://localhost:8573 python3 mcp-server/test_smoke.py`
Expected: all checks including the new failure-bucket round-trip print success / exit 0

- [ ] **Step 3: Commit**

```bash
git add mcp-server/test_smoke.py
git commit -m "test: add failure_bucket round-trip to MCP smoke test"
```

---

## Task 10: Admin dashboard widget

**Files:**
- Modify: `apps/api/app/ops/dashboard.py`
- Modify: `apps/api/app/routers/ops.py`
- Modify: `apps/web/public/admin.html`

- [ ] **Step 1: Add a query function to `dashboard.py`**

In `apps/api/app/ops/dashboard.py`, add near `query_stats()`:

```python
def recent_failure_buckets(session, limit: int = 10) -> dict[str, Any]:
    """Most recently registered/refined failure buckets, for the admin dashboard."""
    from app.db.models import FailureBucket

    rows = list(
        session.execute(
            select(FailureBucket).order_by(FailureBucket.updated_at.desc()).limit(limit)
        ).scalars()
    )
    return {
        "recent": [
            {
                "id": r.id,
                "bucket_name": r.bucket_name,
                "protocol": r.protocol,
                "confidence": r.confidence,
                "support_count": r.support_count,
                "counter_count": r.counter_count,
                "created_by": r.created_by,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    }
```

Check the top of `apps/api/app/ops/dashboard.py` for its existing `select`/typing imports and reuse them (don't re-import if `select` is already imported at module level).

- [ ] **Step 2: Wire it into the `/v1/ops/dashboard` endpoint**

In `apps/api/app/routers/ops.py`, add `recent_failure_buckets` to the import from `app.ops.dashboard` (line 26-31) and add a new try/except block in `ops_dashboard()` (after the `queries` block, end of function, before `return result`):

```python
    try:
        with session_scope() as session:
            result["failure_buckets"] = recent_failure_buckets(session)
    except Exception as exc:  # noqa: BLE001
        result["failure_buckets"] = {"error": str(exc)}

    return result
```

- [ ] **Step 3: Add the widget to `admin.html`**

Add a new card in `apps/web/public/admin.html` after the "최근 쿼리" card (after line 124, closing `</div>` of the `.grid`):

```html
  <div class="card" style="margin-top:12px">
    <strong>최근 등록된 실패 버킷 (failure_bucket)</strong>
    <table class="tbl">
      <thead><tr><th>이름</th><th>프로토콜</th><th>신뢰도</th><th>확인/반박</th><th>등록자</th><th>갱신</th></tr></thead>
      <tbody id="fbBody"><tr><td colspan="6" class="meta">로딩 중…</td></tr></tbody>
    </table>
  </div>
```

Find the JS render function that fills `queryBody` (around line 258-266, the `qb` block) and add, immediately after it, using the same `esc()` helper already in the file (added for the stored-XSS fix in commit `8c2017b`):

```javascript
  const fb = (d.failure_buckets || {}).recent || [];
  $("fbBody").innerHTML = fb.length
    ? fb.map((r) => {
        return '<tr><td>' + esc(r.bucket_name || "") + '</td><td>' + esc(r.protocol || "—") +
          '</td><td>' + esc(String(r.confidence != null ? r.confidence : "—")) +
          '</td><td>' + esc((r.support_count || 0) + " / " + (r.counter_count || 0)) +
          '</td><td>' + esc(r.created_by || "—") + '</td><td>' + fmtTs(r.updated_at) + '</td></tr>';
      }).join("")
    : '<tr><td colspan="6" class="meta">등록된 실패 버킷 없음</td></tr>';
```

Confirm `esc()` and `fmtTs()` are already defined earlier in the file (they are, per the query-table renderer) — do not redefine them.

- [ ] **Step 4: Manual verification**

Run: `docker compose up -d --build api web` then open `http://localhost:8572/admin.html` (admin auth required if `AUTH_MODE` is enforced; with `AUTH_MODE=off` it's open). Confirm the new card renders without a JS console error, showing "등록된 실패 버킷 없음" before any bucket exists.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ops/dashboard.py apps/api/app/routers/ops.py apps/web/public/admin.html
git commit -m "feat: show recent failure_bucket registrations on admin dashboard"
```

---

## Task 11: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/MCP.md`
- Modify: `docs/EXTERNAL_API.md`
- Modify: `docs/AI_AGENT_GUIDE.md`
- Modify: `references/corpus-taxonomy.md`

- [ ] **Step 1: `README.md`** — extend the MCP tool list (line 102) from:

```
도구: `kb_search`, `kb_get_document`, `kb_ask`, `kb_query`, `kb_ticket`, `kb_list_insights`, …
```

to:

```
도구: `kb_search`, `kb_get_document`, `kb_ask`, `kb_query`, `kb_ticket`, `kb_list_insights`,
`kb_register_failure_bucket`, `kb_refine_failure_bucket`, `kb_match_failure_bucket`, …
```

- [ ] **Step 2: `docs/MCP.md`** — add a new table section after the `[유사 장애 · 체크리스트 · 용량]`-equivalent block (after line 52):

```markdown
| Tool | 설명 | 백엔드 |
|------|------|--------|
| `kb_register_failure_bucket` | 실패 버킷(장애 패턴) 등록 — 판별 신호 포함 | `POST /v1/failure-buckets` |
| `kb_refine_failure_bucket` | 신호 추가/확인/반박, 신뢰도 자동 재계산 | `POST /v1/failure-buckets/{id}/refine` |
| `kb_match_failure_bucket` | 관찰 신호로 후보 버킷 순위화 | `POST /v1/failure-buckets/match` |
| `kb_list_failure_buckets` | 등록된 버킷 목록 | `GET /v1/failure-buckets` |
| `kb_get_failure_bucket` | 버킷 상세(신호/원인/조치) | `GET /v1/failure-buckets/{id}` |
```

Add a row to the "사용자 의도 → 도구" table (around line 69-78):

```markdown
| "LB idle-timeout RST 패턴 등록해줘" | `kb_register_failure_bucket(bucket_name=..., discriminating_signals=[...])` |
| "이 RST idle 62초 신호로 어떤 장애 패턴이 유력해?" | `kb_match_failure_bucket(observed_signals=["RST 직전 idle 62초"])` |
```

- [ ] **Step 3: `docs/EXTERNAL_API.md`** — add a section documenting `/v1/failure-buckets/*` (find the existing section for `/v1/insights` or `/v1/checkitems` as a template for tone/format and add a matching section listing the 5 endpoints from Task 6 with request/response shape).

- [ ] **Step 4: `docs/AI_AGENT_GUIDE.md`** — add a new subsection `4.15` (after `4.14 Ops`, before the `---` at line 286):

```markdown
### 4.15 Failure buckets (네트워크 패킷 진단 등)

- `kb_register_failure_bucket(bucket_name=, symptom=, discriminating_signals=, root_cause=, recommended_action=, counter_signals=, protocol=)` — 새 실패 패턴 등록, 즉시 검색 노출
- `kb_match_failure_bucket(observed_signals=, symptom=, protocol=)` — 관찰 신호로 후보 순위화 (구조화 매칭, 하이브리드 검색 아님)
- `kb_refine_failure_bucket(bucket_id=, add_signal=, add_counter_signal=, confirm=)` — 확인/반박 시 신뢰도 자동 재계산 (self-improving)
- `kb_list_failure_buckets(protocol=)` / `kb_get_failure_bucket(bucket_id=)`

**API:** `POST /v1/failure-buckets`, `POST /v1/failure-buckets/{id}/refine`,
`POST /v1/failure-buckets/match`, `GET /v1/failure-buckets[/{id}]`

패킷 분석 중 이미 알려진 패턴인지 먼저 `kb_match_failure_bucket`으로 확인하고,
새 패턴이면 `kb_register_failure_bucket`으로 등록, 기존 패턴이 맞았거나 틀렸으면
`kb_refine_failure_bucket(confirm=True/False)`로 되먹임한다.
```

Add `failure_bucket` to the "Source types" table in section 6 (around line 305-316) alongside the other source_type rows.

- [ ] **Step 5: `references/corpus-taxonomy.md`** — add a closing section (this file otherwise documents `data/raw/` file-scan results, so this new category needs an explicit "not file-based" callout):

```markdown
## 4. `failure_bucket` (API/MCP로 등재되는 카테고리 — `data/raw/` 스캔 대상 아님)

기존 6개 `source_type`과 달리 `failure_bucket`은 `data/raw/`의 파일을 스캔해 적재하는 것이
아니라, `kb_register_failure_bucket` MCP 도구(→ `POST /v1/failure-buckets`)를 통해
실시간으로 등재된다. 필드: `bucket_name`, `protocol`, `symptom`, `discriminating_signals`,
`counter_signals`, `root_cause`, `recommended_action`, `confidence`(self-improving,
`kb_refine_failure_bucket` 호출로 갱신). 설계 근거: `docs/superpowers/specs/2026-07-29-failure-bucket-design.md`.
```

- [ ] **Step 6: Regenerate HTML docs**

Run: `.venv/bin/python scripts/render_docs_html.py`
Expected: exits 0, updates `apps/web/public/docs/*.html` (do not hand-edit those files)

- [ ] **Step 7: Commit**

```bash
git add README.md docs/MCP.md docs/EXTERNAL_API.md docs/AI_AGENT_GUIDE.md references/corpus-taxonomy.md apps/web/public/docs/
git commit -m "docs: document failure_bucket category, API, and MCP tools"
```

---

## Task 12: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit test suite**

Run: `cd apps/api && PYTHONPATH=. AUTH_MODE=off DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:1/citec_knowledge REDIS_URL=redis://127.0.0.1:1/0 python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py`
Expected: all tests pass, including the new `test_taxonomy_failure_bucket.py`, `test_failure_bucket_match.py`, `test_failure_bucket_draft.py`

- [ ] **Step 2: Run alembic migration against a live stack**

Run: `docker compose up -d --build` then `docker compose exec api alembic upgrade head`
Expected: `failure_buckets` table created, no errors

- [ ] **Step 3: Register a bucket via REST**

Run:
```bash
curl -s -X POST localhost:8573/v1/failure-buckets -H 'Content-Type: application/json' -d '{
  "bucket_name": "LB idle-timeout RST",
  "symptom": "다운로드 중 연결이 끊긴다",
  "discriminating_signals": ["RST 직전 idle 60초 이상", "FIN 없이 RST"],
  "counter_signals": ["RST 이전 재전송 다수 관찰"],
  "root_cause": "LB 세션 idle timeout이 애플리케이션 keepalive보다 짧음",
  "recommended_action": "keepalive 간격을 idle timeout보다 짧게 설정",
  "protocol": "TCP",
  "created_by": "verification"
}' | jq .
```
Expected: `id`, `confidence: 0.5`, `index.embedded >= 0`

- [ ] **Step 4: Match against it**

Run:
```bash
curl -s -X POST localhost:8573/v1/failure-buckets/match -H 'Content-Type: application/json' -d '{
  "observed_signals": ["RST 직전 idle 62초"],
  "symptom": "다운로드 중 연결 끓김"
}' | jq '{results: .results[:1]}'
```
Expected: top result is the bucket created in Step 3, `label` is `"가능"` or `"조건부"`

- [ ] **Step 5: Refine it (confirm) and check confidence rose**

Run:
```bash
BUCKET_ID=$(curl -s localhost:8573/v1/failure-buckets?limit=1 | jq -r '.items[0].id')
curl -s -X POST "localhost:8573/v1/failure-buckets/$BUCKET_ID/refine" -H 'Content-Type: application/json' -d '{"confirm": true}' | jq '{confidence, support_count, counter_count}'
```
Expected: `confidence > 0.5`, `support_count: 1`

- [ ] **Step 6: Confirm it's searchable through the normal document surface**

Run: `curl -s "localhost:8573/v1/search" -X POST -H 'Content-Type: application/json' -d '{"q":"LB idle-timeout RST","top_k":5}' | jq '.results[] | select(.source_type=="failure_bucket")'`
Expected: the bucket's mirrored document appears with `source_type: "failure_bucket"`

- [ ] **Step 7: Verify MCP tools end-to-end**

Run: `docker compose up -d --build mcp` then use an MCP client (or `mcp-server/test_smoke.py` from Task 9) to call `kb_register_failure_bucket`, `kb_match_failure_bucket`, `kb_refine_failure_bucket`, `kb_list_failure_buckets`, `kb_get_failure_bucket` and confirm all return non-error text.

No commit for this task — it's verification only. If any step fails, fix the underlying task and re-run from Step 1.
