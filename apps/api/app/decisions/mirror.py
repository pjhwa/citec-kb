"""Searchable Document mirror of a confirmed decision (same pattern as failure_buckets/draft.py).

Hash stability: the draft body/metadata hold only what defines the *meaning* of the standard
(question, conclusion, who/when, reason, evidence, rejected options). Status flags that change
without changing meaning (needs_recheck, review interval, queue score, timing) are written to
the Document row's metadata after the upsert, so they never trigger a re-chunk / re-embed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select

from app.db.models import Decision, DecisionEvidence, DecisionVerdict, Document
from app.db.session import session_scope
from app.doc_access import document_path
from app.ingest.adapters import DocumentDraft

logger = logging.getLogger("citec.decisions")

SOURCE_TYPE = "confirmed_decision"
GRADE = "H"  # human-confirmed


def external_id(decision_id: str) -> str:
    return f"DEC-{decision_id[:8]}"


def _label(opts: list[dict], key: str) -> str:
    return next((o["label"] for o in opts if o["key"] == key), key)


def body_md(
    dec: Decision, verdict: DecisionVerdict, evidence: list[dict[str, Any]],
    previous_ids: list[str],
) -> str:
    conclusion = verdict.free_text if verdict.option_key == "OTHER" else _label(dec.options, verdict.option_key)
    by = f"{verdict.decided_by} (자기 신고)" if verdict.auth_via == "anonymous" else verdict.decided_by
    lines = [
        f"# [확정] {dec.question}", "",
        f"결론: {conclusion}",
        f"확정일: {verdict.decided_at.date().isoformat()}",
        f"확정자: {by}",
    ]
    if verdict.external_only_confirmed:
        lines.append("주의: 외부·서버 미검증 근거만으로 확정됨")
    if verdict.rationale:
        lines += ["", f"변경 이유: {verdict.rationale}"]
    if previous_ids:
        lines += ["", "이전 기준: " + ", ".join(f"{external_id(i)} (confirmed_decision/{external_id(i)}.md)" for i in previous_ids)]
    lines += ["", "근거:"]
    for e in evidence:
        where = e["path"] if e["path"] else e["url"]
        lines.append(f"- [{e['stance']}] {where} — “{e['quote']}”")
    rest = [o for o in dec.options if o["key"] not in (verdict.option_key, "NEI", "OTHER")]
    if rest:
        lines += ["", "선택하지 않은 선택지:"]
        lines += [f"- {o['label']}" + (f": {o['summary']}" if o.get("summary") else "") for o in rest]
    return "\n".join(lines)


def build_draft(dec: Decision, verdict: DecisionVerdict, evidence: list[dict], previous_ids: list[str]) -> DocumentDraft:
    return DocumentDraft(
        source_type=SOURCE_TYPE,
        external_id=external_id(dec.id),
        title=dec.question,
        body_md=body_md(dec, verdict, evidence, previous_ids),
        metadata={"decision_id": dec.id, "topic": dec.topic},
        evidence_grade=GRADE,
        source_uri=f"decision://{dec.id}",
        domain=dec.domain,
    ).finalize()


_DOC_STATUS = {"confirmed": "active", "needs_recheck": "active", "superseded": "superseded", "retired": "archived"}


def sync(decision_id: str, *, embed: bool = True) -> Optional[dict[str, Any]]:
    """Upsert the mirror for a decision that has a current verdict; no-op otherwise."""
    from app.ingest.pipeline import upsert_document_from_draft

    with session_scope() as s:
        dec = s.get(Decision, decision_id)
        if not dec or not dec.current_verdict_id:
            return None
        verdict = s.get(DecisionVerdict, dec.current_verdict_id)
        evs = list(s.scalars(select(DecisionEvidence).where(
            DecisionEvidence.decision_id == decision_id, DecisionEvidence.retracted_at.is_(None))
            .order_by(DecisionEvidence.created_at)).all())
        docs = {d.id: d for d in s.scalars(select(Document).where(
            Document.id.in_([e.document_id for e in evs if e.document_id] or [""]))).all()}
        ev_view = [{
            "stance": e.stance, "quote": e.quote,
            "path": document_path(external_id=docs[e.document_id].external_id,
                                  source_type=docs[e.document_id].source_type) if e.document_id in docs else "",
            "url": e.external_url or "",
        } for e in evs]
        prev = list(s.scalars(select(Decision.id).where(Decision.superseded_by == decision_id)).all())
        draft = build_draft(dec, verdict, ev_view, prev)
        status = dec.status
        flags = {
            "decision_status": status,
            "recheck": status == "needs_recheck",
            "decided_by": verdict.decided_by,
            "self_reported": verdict.auth_via == "anonymous",
            "decided_at": verdict.decided_at.isoformat(),
            "superseded_by": dec.superseded_by,
        }
    res = upsert_document_from_draft(draft)
    doc_id = res["document_id"]
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        doc.status = _DOC_STATUS.get(status, "active")
        doc.metadata_ = {**(doc.metadata_ or {}), **flags}
    if embed:
        _embed(doc_id)
    return {"document_id": doc_id, "action": res.get("action"), "content_hash": res.get("content_hash")}


def _embed(document_id: str) -> None:
    try:
        from app.embed.job import embed_pending_chunks

        embed_pending_chunks(document_id=document_id, batch_size=16)
    except Exception:  # noqa: BLE001 — 검색 미러의 임베딩 실패가 판정을 되돌리지 않는다
        logger.exception("embed decision mirror failed doc=%s", document_id)


def safe_sync(decision_id: str) -> Optional[str]:
    """Run sync, return an error string instead of raising (the verdict row is already durable)."""
    try:
        sync(decision_id)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.exception("decision mirror sync failed id=%s", decision_id)
        return str(exc)
