"""Decision store: propose → evidence (gated) → verdict (append-only) → supersede/retire."""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import func, select

from app.db.models import Decision, DecisionEvidence, DecisionNonce, DecisionVerdict, Document
from app.db.session import session_scope
from app.decisions import rules
from app.settings import get_settings

NONCE_TTL_SECONDS = 60
EXTERNAL_SNAPSHOT_MAX = 4000


class DecisionNotFound(KeyError):
    pass


class DecisionConflict(Exception):
    """State does not allow the action (HTTP 409)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


def _qhash(quote: str) -> str:
    return hashlib.sha256(rules.norm_text(quote).encode()).hexdigest()[:32]


def resolve_actor(principal: Any, claimed: Optional[str]) -> tuple[str, str]:
    """(decided_by, auth_via). Enforced auth ignores the claimed name (anti-forgery)."""
    via = getattr(principal, "auth_via", "anonymous")
    if via != "anonymous":
        return principal.name, via
    name = (claimed or "").strip()
    if not name:
        raise ValueError("decided_by is required while authentication is off (self-reported)")
    return name, "anonymous"


def _queue_weights() -> dict[str, float]:
    try:
        p = Path(get_settings().config_dir) / "decision_queue.json"
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:  # noqa: BLE001 — 설정 오류가 큐를 막지 않게 기본 가중치로
        return {}


# ---------------------------------------------------------------- propose / evidence

def create_decision(
    *, question: str, options: list[dict], topic: Optional[str] = None, domain: Optional[str] = None,
    stakes: str = "mid", ai_recommendation_key: Optional[str] = None,
    review_interval_days: Optional[int] = 180, created_by_kind: str = "mcp",
    created_by: Optional[str] = None, evidence: Optional[list[dict]] = None,
) -> dict[str, Any]:
    if stakes not in rules.STAKES:
        raise ValueError(f"stakes must be one of {list(rules.STAKES)}")
    opts, warnings = rules.lint_options(question, options)
    keys = {o["key"] for o in opts}
    if ai_recommendation_key and ai_recommendation_key not in keys:
        raise ValueError(f"ai_recommendation_key {ai_recommendation_key!r} not in options")
    with session_scope() as s:
        row = Decision(
            question=rules.norm_text(question), options=opts, topic=topic, domain=domain,
            stakes=stakes, status="proposed", ai_recommendation_key=ai_recommendation_key,
            review_interval_days=review_interval_days, created_by_kind=created_by_kind,
            created_by=created_by,
        )
        s.add(row)
        s.flush()
        did = row.id
    for ev in evidence or []:
        add_evidence(did, added_by=created_by, **ev)
    out = get_decision(did)
    out["warnings"] = warnings
    return out


def _validate_external_url(url: str) -> None:
    u = urlparse(url or "")
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("external_url must be http(s)")


def add_evidence(
    decision_id: str, *, quote: str, stance: str, option_key: Optional[str] = None,
    document_id: Optional[str] = None, quote_locator: Optional[str] = None,
    external_url: Optional[str] = None, external_snapshot: Optional[str] = None,
    retrieved_at: Optional[datetime] = None, added_by: Optional[str] = None,
) -> dict[str, Any]:
    if stance not in rules.STANCES:
        raise ValueError(f"stance must be one of {list(rules.STANCES)}")
    if not rules.norm_text(quote):
        raise ValueError("quote is required")
    with session_scope() as s:
        dec = s.get(Decision, decision_id)
        if not dec:
            raise DecisionNotFound(decision_id)
        if dec.status in ("superseded", "retired"):
            raise DecisionConflict(f"cannot add evidence to a {dec.status} decision")
        if option_key is not None and option_key not in {o["key"] for o in dec.options}:
            raise ValueError(f"option_key {option_key!r} not in decision options")
        fields: dict[str, Any] = {}
        if document_id:
            doc = s.get(Document, document_id)
            if not doc:
                raise ValueError(f"document_id not found: {document_id}")
            if not rules.quote_in_body(quote, doc.body_md):
                raise ValueError("quote not found in the pinned document body (after NFC/whitespace normalisation)")
            md = doc.metadata_ or {}
            fields = dict(
                document_id=doc.id, origin="internal", pinned_version=doc.version,
                pinned_content_hash=doc.content_hash, grade_snapshot=doc.evidence_grade,
                source_modified_at=_parse_dt(md.get("source_modified_at")) or doc.updated_at,
            )
        elif external_url:
            _validate_external_url(external_url)
            if not external_snapshot or not retrieved_at:
                raise ValueError("external evidence needs external_snapshot and retrieved_at")
            if len(external_snapshot) > EXTERNAL_SNAPSHOT_MAX:
                raise ValueError(f"external_snapshot exceeds {EXTERNAL_SNAPSHOT_MAX} chars")
            fields = dict(
                origin="external", external_url=external_url, external_snapshot=external_snapshot,
                retrieved_at=retrieved_at,
            )
        else:
            raise ValueError("either document_id (internal) or external_url (external) is required")
        qh = _qhash(quote)
        dup = s.scalar(
            select(DecisionEvidence.id).where(
                DecisionEvidence.decision_id == decision_id,
                DecisionEvidence.option_key.is_(option_key) if option_key is None else DecisionEvidence.option_key == option_key,
                DecisionEvidence.quote_hash == qh,
                DecisionEvidence.retracted_at.is_(None),
                DecisionEvidence.document_id == fields.get("document_id")
                if fields.get("document_id") else DecisionEvidence.external_url == fields.get("external_url"),
            )
        )
        if dup:
            raise DecisionConflict(f"duplicate evidence: {dup}")
        ev = DecisionEvidence(
            decision_id=decision_id, option_key=option_key, quote=quote.strip(), quote_hash=qh,
            quote_locator=quote_locator, stance=stance, added_by=added_by, **fields,
        )
        s.add(ev)
        s.flush()
        return _ev_dict(ev)


def retract_evidence(decision_id: str, evidence_id: str) -> dict[str, Any]:
    with session_scope() as s:
        ev = s.get(DecisionEvidence, evidence_id)
        if not ev or ev.decision_id != decision_id:
            raise DecisionNotFound(evidence_id)
        ev.retracted_at = ev.retracted_at or _now()
        s.flush()
        return _ev_dict(ev)


def _parse_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


# ---------------------------------------------------------------- read

def _ev_dict(e: DecisionEvidence, current: Optional[dict] = None) -> dict[str, Any]:
    d = {
        "id": e.id, "option_key": e.option_key, "stance": e.stance, "origin": e.origin,
        "quote": e.quote, "quote_locator": e.quote_locator, "document_id": e.document_id,
        "pinned_version": e.pinned_version, "pinned_content_hash": e.pinned_content_hash,
        "grade_snapshot": e.grade_snapshot, "source_modified_at": _iso(e.source_modified_at),
        "external_url": e.external_url, "external_snapshot": e.external_snapshot,
        "retrieved_at": _iso(e.retrieved_at), "added_by": e.added_by,
        "retracted_at": _iso(e.retracted_at), "unverified_external": e.origin == "external",
    }
    if current is not None:
        d["current_version"] = current.get("version")
        d["changed"] = bool(e.document_id) and current.get("content_hash") != e.pinned_content_hash
        d["source_type"] = current.get("source_type")
        d["external_id"] = current.get("external_id")
        d["title"] = current.get("title")
        d["access"] = current.get("access")
    return d


def _verdict_dict(v: DecisionVerdict) -> dict[str, Any]:
    return {
        "id": v.id, "option_key": v.option_key, "free_text": v.free_text, "rationale": v.rationale,
        "decided_by": v.decided_by, "auth_via": v.auth_via, "self_reported": v.auth_via == "anonymous",
        "decided_via": v.decided_via, "decided_at": _iso(v.decided_at),
        "ai_recommendation_key": v.ai_recommendation_key, "time_to_decide_ms": v.time_to_decide_ms,
        "supersedes_verdict_id": v.supersedes_verdict_id, "external_only_confirmed": v.external_only_confirmed,
    }


def _current_docs(s, ids: set[str]) -> dict[str, dict]:
    if not ids:
        return {}
    from app.doc_access import document_access

    rows = s.execute(
        select(Document.id, Document.version, Document.content_hash, Document.source_type,
               Document.external_id, Document.title, Document.source_uri).where(Document.id.in_(ids))
    ).all()
    return {
        r.id: {
            "version": r.version, "content_hash": r.content_hash, "source_type": r.source_type,
            "external_id": r.external_id, "title": r.title,
            "access": document_access(external_id=r.external_id, source_type=r.source_type,
                                      document_id=r.id, title=r.title, source_uri=r.source_uri),
        }
        for r in rows
    }


def _payload(s, dec: Decision, *, full: bool) -> dict[str, Any]:
    evs = list(s.scalars(select(DecisionEvidence).where(DecisionEvidence.decision_id == dec.id)
                         .order_by(DecisionEvidence.created_at)).all())
    docs = _current_docs(s, {e.document_id for e in evs if e.document_id})
    ev_dicts = [_ev_dict(e, docs.get(e.document_id)) for e in evs]
    live = [e for e in ev_dicts if not e["retracted_at"]]
    changed = sum(1 for e in live if e.get("changed"))
    verdicts = list(s.scalars(select(DecisionVerdict).where(DecisionVerdict.decision_id == dec.id)
                              .order_by(DecisionVerdict.decided_at)).all())
    cur = next((v for v in verdicts if v.id == dec.current_verdict_id), None)
    base = cur.decided_at if cur else dec.created_at
    age_days = max(0.0, (_now() - base).total_seconds() / 86400)
    overdue = bool(dec.review_interval_days and cur and age_days > dec.review_interval_days)
    eff = dec.status
    if dec.status == "confirmed" and (changed or overdue):
        eff = "needs_recheck"
    cf = rules.conflict_flag(live)
    score, why = rules.queue_score(
        conflict=cf["conflict"], stakes=dec.stakes, age_days=age_days,
        review_interval_days=dec.review_interval_days if cur else None, changed_pins=changed,
        weights=_queue_weights(),
    )
    out = {
        "id": dec.id, "question": dec.question, "options": dec.options, "topic": dec.topic,
        "domain": dec.domain, "stakes": dec.stakes, "status": dec.status, "effective_status": eff,
        "recheck_reasons": (["근거 문서 변경"] if changed else []) + (["확인 주기 경과"] if overdue else []),
        "ai_recommendation_key": dec.ai_recommendation_key, "superseded_by": dec.superseded_by,
        "review_interval_days": dec.review_interval_days, "created_by": dec.created_by,
        "created_by_kind": dec.created_by_kind, "created_at": _iso(dec.created_at),
        "current_verdict": _verdict_dict(cur) if cur else None,
        "conflict": cf, "evidence_count": len(live),
        "source_count": len({e.get("document_id") or e.get("external_url") for e in live}),
        "changed_pins": changed, "queue_score": score, "queue_reason": why,
        "external_only": bool(live) and all(e["origin"] == "external" for e in live),
    }
    if full:
        by_opt: dict[str, list] = {}
        for e in ev_dicts:
            by_opt.setdefault(e["option_key"] or "_common", []).append(e)
        out["evidence_by_option"] = by_opt
        out["history"] = [_verdict_dict(v) for v in verdicts]
        prev = s.scalars(select(Decision.id).where(Decision.superseded_by == dec.id)).all()
        out["supersedes"] = list(prev)
    return out


def get_decision(decision_id: str) -> dict[str, Any]:
    with session_scope() as s:
        dec = s.get(Decision, decision_id)
        if not dec:
            raise DecisionNotFound(decision_id)
        return _payload(s, dec, full=True)


def list_decisions(
    *, status: Optional[str] = None, topic: Optional[str] = None, domain: Optional[str] = None,
    sort: str = "priority", include_superseded: bool = False, limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    if status and status not in rules.STATUSES:
        raise ValueError(f"invalid status {status!r}; allowed {list(rules.STATUSES)}")
    if sort not in ("priority", "recent"):
        raise ValueError("sort must be priority|recent")
    with session_scope() as s:
        stmt = select(Decision)
        if status:
            stmt = stmt.where(Decision.status == status)
        else:
            hidden = ["retired"] + ([] if include_superseded else ["superseded"])
            stmt = stmt.where(Decision.status.notin_(hidden))
        if topic:
            stmt = stmt.where(Decision.topic == topic)
        if domain:
            stmt = stmt.where(Decision.domain == domain)
        rows = list(s.scalars(stmt.order_by(Decision.created_at.desc())).all())
        items = [_payload(s, d, full=False) for d in rows]
    if sort == "priority":
        # 선입선출이 아니라 충돌·위험·낡음 순. 이미 판정된 신선한 항목은 뒤로 간다.
        def key(i):
            settled = 1 if (i["effective_status"] == "confirmed") else 0
            return (settled, -i["queue_score"])
        items.sort(key=key)
    total = len(items)
    return {"total": total, "limit": limit, "offset": offset, "items": items[offset: offset + limit]}


# ---------------------------------------------------------------- verdict

def issue_nonce(decision_id: str) -> dict[str, Any]:
    with session_scope() as s:
        if not s.get(Decision, decision_id):
            raise DecisionNotFound(decision_id)
        n = DecisionNonce(decision_id=decision_id, expires_at=_now() + timedelta(seconds=NONCE_TTL_SECONDS))
        s.add(n)
        s.flush()
        return {"nonce": n.id, "expires_at": _iso(n.expires_at), "ttl_seconds": NONCE_TTL_SECONDS}


def _consume_nonce(s, decision_id: str, nonce: str) -> None:
    n = s.get(DecisionNonce, nonce, with_for_update=True)
    if not n or n.decision_id != decision_id or n.used_at is not None or n.expires_at < _now():
        raise PermissionError("invalid, expired or already used nonce")
    n.used_at = _now()


def add_verdict(
    decision_id: str, *, option_key: Optional[str], decided_by: str, auth_via: str, nonce: str,
    rationale: Optional[str] = None, free_text: Optional[str] = None, keep: bool = False,
    external_only_confirm: bool = False, time_to_decide_ms: Optional[int] = None,
) -> dict[str, Any]:
    with session_scope() as s:
        dec = s.get(Decision, decision_id, with_for_update=True)
        if not dec:
            raise DecisionNotFound(decision_id)
        if dec.status in ("superseded", "retired"):
            raise DecisionConflict(f"decision is {dec.status}")
        _consume_nonce(s, decision_id, nonce)
        cur = s.get(DecisionVerdict, dec.current_verdict_id) if dec.current_verdict_id else None
        if keep:
            if not cur:
                raise ValueError("keep requires an existing verdict")
            option_key = cur.option_key
            free_text = free_text or cur.free_text
        if option_key not in {o["key"] for o in dec.options}:
            raise ValueError(f"option_key {option_key!r} not in decision options")
        if option_key == "OTHER" and not rules.norm_text(free_text):
            raise ValueError("free_text is required for OTHER")

        evs = list(s.scalars(select(DecisionEvidence).where(
            DecisionEvidence.decision_id == decision_id, DecisionEvidence.retracted_at.is_(None))).all())
        if keep:
            _repin(s, dec, evs)
            evs = list(s.scalars(select(DecisionEvidence).where(
                DecisionEvidence.decision_id == decision_id, DecisionEvidence.retracted_at.is_(None))).all())
        if option_key != "NEI":
            if not evs:
                raise ValueError("a confirmation needs at least one evidence item")
            ext_only = all(e.origin == "external" for e in evs)
            if ext_only and not external_only_confirm:
                raise ValueError("only external (unverified) evidence: set external_only_confirm=true")
        else:
            ext_only = False
        v = DecisionVerdict(
            decision_id=decision_id, option_key=option_key, free_text=free_text, rationale=rationale,
            decided_by=decided_by, auth_via=auth_via, decided_via="ui",
            ai_recommendation_key=dec.ai_recommendation_key, time_to_decide_ms=time_to_decide_ms,
            evidence_snapshot=[{"id": e.id, "document_id": e.document_id,
                                "content_hash": e.pinned_content_hash} for e in evs],
            supersedes_verdict_id=dec.current_verdict_id, external_only_confirmed=ext_only and option_key != "NEI",
        )
        s.add(v)
        s.flush()
        if option_key != "NEI":  # Q6: NEI는 이력만 남기고 기준이 되지 않는다
            dec.current_verdict_id = v.id
            dec.status = "confirmed"
        s.flush()
        return {"verdict": _verdict_dict(v), "status": dec.status}


def _repin(s, dec: Decision, evs: list[DecisionEvidence]) -> None:
    """유지: 바뀐 근거를 현재 버전으로 다시 고정한다(옛 행은 철회, 새 행 추가)."""
    for e in evs:
        if not e.document_id:
            continue
        doc = s.get(Document, e.document_id)
        if doc is None:
            raise DecisionConflict(f"evidence document missing: {e.document_id}")
        if doc.content_hash == e.pinned_content_hash:
            continue
        if not rules.quote_in_body(e.quote, doc.body_md):
            raise DecisionConflict(
                f"evidence {e.id}: quote no longer present in the current document; review or retract it first"
            )
        e.retracted_at = _now()
        s.add(DecisionEvidence(
            decision_id=dec.id, option_key=e.option_key, document_id=doc.id, pinned_version=doc.version,
            pinned_content_hash=doc.content_hash, quote=e.quote, quote_hash=e.quote_hash,
            quote_locator=e.quote_locator, grade_snapshot=doc.evidence_grade, stance=e.stance,
            origin="internal", added_by=e.added_by, source_modified_at=doc.updated_at,
        ))
    s.flush()


# ---------------------------------------------------------------- lifecycle

def supersede(decision_id: str, *, new_decision_id: str) -> dict[str, Any]:
    if decision_id == new_decision_id:
        raise ValueError("a decision cannot supersede itself")
    with session_scope() as s:
        old = s.get(Decision, decision_id, with_for_update=True)
        new = s.get(Decision, new_decision_id)
        if not old or not new:
            raise DecisionNotFound(decision_id if not old else new_decision_id)
        if old.status not in ("confirmed", "needs_recheck"):
            raise DecisionConflict(f"only confirmed/needs_recheck can be superseded (is {old.status})")
        if new.status in ("superseded", "retired"):
            raise DecisionConflict(f"replacement decision is {new.status}")
        old.status = "superseded"
        old.superseded_by = new.id
        s.flush()
        return {"id": old.id, "status": old.status, "superseded_by": new.id}


def retire(decision_id: str) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Decision, decision_id, with_for_update=True)
        if not d:
            raise DecisionNotFound(decision_id)
        if d.status == "retired":
            raise DecisionConflict("already retired")
        d.status_before_retire = d.status
        d.status = "retired"
        s.flush()
        return {"id": d.id, "status": d.status}


def restore(decision_id: str) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Decision, decision_id, with_for_update=True)
        if not d:
            raise DecisionNotFound(decision_id)
        if d.status != "retired":
            raise DecisionConflict("only retired decisions can be restored")
        d.status = d.status_before_retire or "proposed"
        d.status_before_retire = None
        s.flush()
        return {"id": d.id, "status": d.status}


def scan_staleness() -> dict[str, Any]:
    """confirmed → needs_recheck when a pinned document changed or the review interval elapsed."""
    changed_ids: list[str] = []
    with session_scope() as s:
        for d in s.scalars(select(Decision).where(Decision.status == "confirmed")).all():
            p = _payload(s, d, full=False)
            if p["effective_status"] == "needs_recheck":
                d.status = "needs_recheck"
                changed_ids.append(d.id)
        s.flush()
    return {"marked_needs_recheck": changed_ids, "count": len(changed_ids)}


def metrics() -> dict[str, Any]:
    with session_scope() as s:
        by_status = dict(s.execute(select(Decision.status, func.count()).group_by(Decision.status)).all())
        vs = list(s.scalars(select(DecisionVerdict)).all())
    times = [v.time_to_decide_ms for v in vs if v.time_to_decide_ms is not None]
    total = len(vs)
    override = [v for v in vs if v.ai_recommendation_key and v.option_key != v.ai_recommendation_key]
    with_ai = [v for v in vs if v.ai_recommendation_key]
    return {
        "definition": "verdicts = decision_verdicts rows (append-only history); decisions by status = decisions rows",
        "decisions_by_status": by_status,
        "verdicts_total": total,
        "median_time_to_decide_ms": statistics.median(times) if times else None,
        "ai_override_rate": (len(override) / len(with_ai)) if with_ai else None,
        "nei_rate": (sum(1 for v in vs if v.option_key == "NEI") / total) if total else None,
        "needs_recheck": by_status.get("needs_recheck", 0),
        "self_reported_verdicts": sum(1 for v in vs if v.auth_via == "anonymous"),
    }
