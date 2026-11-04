"""Failure-bucket flywheel: register → index → refine (self-improving)."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import Document, FailureBucket
from app.db.session import session_scope
from app.failure_buckets.draft import bucket_draft, compute_confidence
from app.failure_buckets.match import rank_buckets
from app.settings import get_settings
from app.taxonomy import _FB_DOMAIN_TO_CORPUS_DOMAIN

logger = logging.getLogger("citec.failure_buckets")

# Safety cap on match_buckets()'s SQL fetch — Python ranking runs over the
# fetched rows, so this bounds worst-case latency as bucket volume grows
# across plugins (see design doc §9).
_MATCH_FETCH_LIMIT = 500


def _known_fb_domains() -> frozenset[str]:
    """fb_domain values documented in references/failure-bucket-domains.md.

    references/ isn't copied into the API container image (see apps/api/Dockerfile),
    so this can't read that file at runtime — instead it uses
    app.taxonomy._FB_DOMAIN_TO_CORPUS_DOMAIN's keys, which both docs already
    require to be updated in lockstep with failure-bucket-domains.md whenever a
    new fb_domain is added (design doc §2/§5). Soft convention only: an
    unlisted fb_domain is still accepted, just flagged with a warning.
    """
    return frozenset(_FB_DOMAIN_TO_CORPUS_DOMAIN.keys())


_ENVIRONMENTS = ("csp", "msp", "onprem", "hybrid")


def normalize_match_filters(
    fb_domain: Optional[str], environment: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """Lower-case and validate the filters of /failure-buckets/match.

    알 수 없는 fb_domain / environment는 "일치하는 버킷 없음"으로 조용히 끝나지 않고
    허용 값 목록과 함께 ValueError로 거부한다. fb_domain 허용 집합은 문서화된
    값(_known_fb_domains)과 이미 등록된 버킷이 쓰는 값의 합집합이다(미등재 도메인도
    등록은 허용하는 soft convention과 맞춘다).
    """
    dom = (fb_domain or "").strip().lower() or None
    env = (environment or "").strip().lower() or None
    if env and env not in _ENVIRONMENTS:
        raise ValueError(f"invalid environment {environment!r} — allowed: {list(_ENVIRONMENTS)}")
    if dom:
        with session_scope() as session:
            present = {r[0] for r in session.execute(select(FailureBucket.fb_domain).distinct()).all() if r[0]}
        allowed = sorted(set(_known_fb_domains()) | present)
        if dom not in allowed:
            raise ValueError(f"invalid fb_domain {fb_domain!r} — allowed: {allowed}")
    return dom, env


STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"
STATUS_TEST = "test"

# Retire reasons shorter than this are rejected — a retire without a stated
# reason can't be audited later.
MIN_RETIRE_REASON_LEN = 8


class BucketRetiredError(Exception):
    """Raised when a write (refine) targets a retired bucket."""


def _to_dict(row: FailureBucket) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status or STATUS_ACTIVE,
        "retired_at": row.retired_at.isoformat() if row.retired_at else None,
        "retired_by": row.retired_by,
        "retire_reason": row.retire_reason,
        "document_id": row.document_id,
        "bucket_name": row.bucket_name,
        "fb_domain": row.fb_domain,
        "protocol": row.protocol,
        "environment": row.environment,
        "evidence_ref": row.evidence_ref,
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
        # kb_search/kb_query/kb_ask only return Document.status == 'active', and the
        # ingest upsert revives 'archived' docs. So a non-active bucket (retired/test)
        # must have its mirror re-archived after every upsert.
        if row and (row.status or STATUS_ACTIVE) != STATUS_ACTIVE:
            doc = session.get(Document, doc_id)
            if doc is not None:
                doc.status = "archived"
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


_DUPLICATE_SCORE_THRESHOLD = 0.75

# ── system registrants (created_by = "<system>@<version>") ────────────────
_SYSTEM_CREATED_BY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*@[A-Za-z0-9._-]+$")
# "<name>@<version-ish>": a version starts with an optional v and a digit. Used only to
# decide whether an unlisted name@x value looks like an attempted system registrant
# (vs. a human email such as someone@example.com, which gets no warning).
_VERSIONISH_RE = re.compile(r"^v?\d")
_registrants_lock = threading.Lock()
_registrants_cache: tuple[tuple[float, int] | None, frozenset[str]] = (None, frozenset())


def _system_registrants() -> frozenset[str]:
    """System names from config/system_registrants.json (mtime/size cached).

    A missing or malformed file yields an empty set (nobody is classified as a
    system) — registration itself must never fail on this soft convention.
    """
    global _registrants_cache
    path = Path(get_settings().config_dir) / "system_registrants.json"
    try:
        st = path.stat()
        key: tuple[float, int] | None = (st.st_mtime, st.st_size)
    except OSError:
        return frozenset()
    with _registrants_lock:
        if _registrants_cache[0] == key:
            return _registrants_cache[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            names = frozenset(str(n).strip() for n in (data.get("systems") or []) if str(n).strip())
        except Exception:  # noqa: BLE001
            logger.exception("failed to load %s", path)
            names = frozenset()
        _registrants_cache = (key, names)
        return names


def classify_registrant(created_by: Optional[str]) -> tuple[str, Optional[str]]:
    """Return (registrant_kind, warning). Never rejects.

    system: matches `<name>@<version>` AND <name> is on the allowlist.
    human:  everything else; a warning is added only when the value looks like an
            attempted system registrant (bad format, or unlisted name with a
            version-like suffix).
    """
    value = (created_by or "").strip()
    if "@" not in value:
        return "human", None
    if not _SYSTEM_CREATED_BY_RE.match(value):
        return "human", (
            f"created_by='{value}'가 '<시스템>@<판>' 형식"
            "(^[a-z0-9][a-z0-9._-]*@[A-Za-z0-9._-]+$)과 다릅니다. 시스템 등록자는 예: insight-ops@v1.4"
        )
    name, version = value.split("@", 1)
    if name in _system_registrants():
        return "system", None
    if _VERSIONISH_RE.match(version):
        return "human", (
            f"created_by='{value}'의 시스템 이름 '{name}'이 허용 목록(config/system_registrants.json)에 없습니다. "
            "KB 담당자에게 등록을 신청하세요. 현재는 사람 등록(registrant_kind=human)으로 처리됩니다."
        )
    return "human", None

# evidence_ref soft-format check (warning only, not a rejection — see
# multi-plugin design doc §11 "운영 마찰을 줄이기 위한 의도적 선택" and the
# analysis doc §B-3: kept as a warning because retroactively rejecting the
# existing legacy:pre-migration rows would be a breaking change, and because
# new prefixes will legitimately appear as new plugins register buckets).
_EVIDENCE_REF_PREFIXES = (
    "capture:", "confluence:", "citects-", "evtx:", "log:", "swim:", "legacy:", "insight:", "agent-room:",
)
# Accepted without a warning, but a human may not be able to open them later
# (insight: needs an insight-ops login; agent-room: vanishes with the room).
_RESTRICTED_EVIDENCE_PREFIXES = ("insight:", "agent-room:")


def create_bucket(
    *,
    bucket_name: str,
    fb_domain: str,
    symptom: str,
    discriminating_signals: list[str],
    root_cause: str,
    recommended_action: str,
    evidence_ref: str,
    counter_signals: Optional[list[str]] = None,
    protocol: Optional[str] = None,
    environment: Optional[str] = None,
    created_by: Optional[str] = None,
    status: str = STATUS_ACTIVE,
) -> dict[str, Any]:
    fb_domain = fb_domain.strip().lower()
    environment = (environment or "").strip().lower() or None
    dup_candidates = match_buckets(
        observed_signals=discriminating_signals,
        symptom=symptom,
        fb_domain=fb_domain,
        environment=environment,
        top_k=1,
    )

    with session_scope() as session:
        row = FailureBucket(
            bucket_name=bucket_name.strip(),
            fb_domain=fb_domain,
            protocol=(protocol or "").strip() or None,
            environment=environment,
            evidence_ref=evidence_ref.strip(),
            symptom=symptom or "",
            discriminating_signals=list(discriminating_signals or []),
            counter_signals=list(counter_signals or []),
            root_cause=root_cause or "",
            recommended_action=recommended_action or "",
            created_by=created_by,
            status=status,
        )
        session.add(row)
        session.flush()
        bucket_id = row.id

    index = _index_bucket(bucket_id)
    result = get_bucket(bucket_id)
    if result is None:
        raise RuntimeError(f"failure_bucket {bucket_id} vanished after create")
    result["index"] = index

    top_dup = dup_candidates[0] if dup_candidates else None
    if top_dup and top_dup.get("confidence", 0.0) >= _DUPLICATE_SCORE_THRESHOLD:
        result["possible_duplicate_of"] = {
            "bucket_id": top_dup.get("bucket_id"),
            "bucket_name": top_dup.get("bucket_name"),
            "confidence": top_dup.get("confidence"),
        }

    if fb_domain not in _known_fb_domains():
        result["fb_domain_warning"] = (
            f"fb_domain='{fb_domain}'는 references/failure-bucket-domains.md에 없는 새 값입니다. "
            "오타가 아니라면 문서에 추가해 주세요."
        )

    ref_check = evidence_ref.strip().lower()
    if not ref_check.startswith(_EVIDENCE_REF_PREFIXES):
        result["evidence_ref_warning"] = (
            f"evidence_ref='{evidence_ref.strip()}'가 알려진 포인터 형식"
            f"({', '.join(_EVIDENCE_REF_PREFIXES)})과 다릅니다. "
            "사람이 나중에 열어 확인할 수 있는 구체적 포인터인지(자유 서술이 아닌지) 확인해 주세요."
        )

    kind, created_by_warning = classify_registrant(created_by)
    result["registrant_kind"] = kind
    if created_by_warning:
        result["created_by_warning"] = created_by_warning

    if ref_check.startswith(_RESTRICTED_EVIDENCE_PREFIXES) and "confluence:" not in ref_check:
        result["evidence_ref_note"] = (
            "열람 제약 있음(insight:는 insight-ops 로그인 필요, agent-room:은 방 삭제 시 근거 소실). "
            "결과 보고서를 Confluence에 올리고 confluence:<page id>를 병기하길 권장합니다."
        )

    return result


def get_bucket(bucket_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        return _to_dict(row) if row else None


def list_buckets(
    *,
    fb_domain: Optional[str] = None,
    protocol: Optional[str] = None,
    environment: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 20,
    offset: int = 0,
    include_retired: bool = False,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with session_scope() as session:
        stmt = select(FailureBucket).order_by(FailureBucket.created_at.desc())
        if not include_retired:
            stmt = stmt.where(FailureBucket.status == STATUS_ACTIVE)
        if fb_domain:
            stmt = stmt.where(FailureBucket.fb_domain == fb_domain)
        if protocol:
            stmt = stmt.where(FailureBucket.protocol == protocol)
        if environment:
            stmt = stmt.where(FailureBucket.environment == environment)
        if min_confidence:
            stmt = stmt.where(FailureBucket.confidence >= min_confidence)
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(session.scalars(stmt.offset(offset).limit(limit)).all())
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [_to_dict(r) for r in rows],
        }


def refine_bucket(
    bucket_id: str,
    *,
    add_signal: Optional[str] = None,
    add_counter_signal: Optional[str] = None,
    environment: Optional[str] = None,
    confirm: bool = True,
) -> dict[str, Any]:
    signals_changed = False
    environment_changed = False
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if not row:
            raise KeyError(bucket_id)
        if row.status == STATUS_RETIRED:
            raise BucketRetiredError(bucket_id)

        if add_signal and add_signal not in (row.discriminating_signals or []):
            row.discriminating_signals = [*(row.discriminating_signals or []), add_signal]
            signals_changed = True
        if add_counter_signal and add_counter_signal not in (row.counter_signals or []):
            row.counter_signals = [*(row.counter_signals or []), add_counter_signal]
            signals_changed = True
        # environment=None (the default) means "leave untouched" — same convention as
        # add_signal/add_counter_signal. Not part of bucket_draft()'s content_hash inputs
        # (see draft.py), so a change here never needs a re-chunk/re-embed — but it DOES
        # need _index_bucket() re-run to push the new value onto the mirrored Document row
        # (bucket_draft() passes environment straight through; the upsert's "same hash"
        # path patches Document.environment without touching chunks, so this stays cheap).
        if environment is not None:
            new_environment = environment.strip().lower() or None
            if new_environment != row.environment:
                row.environment = new_environment
                environment_changed = True

        if confirm:
            row.support_count = int(row.support_count or 0) + 1
        else:
            row.counter_count = int(row.counter_count or 0) + 1
        row.confidence = compute_confidence(row.support_count, row.counter_count)
        session.flush()

    index = _index_bucket(bucket_id) if (signals_changed or environment_changed) else None
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
    fb_domain: Optional[str] = None,
    protocol: Optional[str] = None,
    environment: Optional[str] = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(FailureBucket).where(FailureBucket.status == STATUS_ACTIVE)
        if fb_domain:
            stmt = stmt.where(FailureBucket.fb_domain == fb_domain)
        if protocol:
            stmt = stmt.where(FailureBucket.protocol == protocol)
        if environment:
            # Hard filter, not a score input (see analysis doc §B-1): a bucket
            # tagged for a different environment shouldn't surface even at low
            # confidence, since applying a pattern to the wrong topology is
            # exactly the failure mode this facet exists to prevent. A bucket
            # with environment=None (not yet confirmed either way) is treated
            # as environment-agnostic and still matches any filter.
            stmt = stmt.where(
                (FailureBucket.environment == environment) | (FailureBucket.environment.is_(None))
            )
        stmt = stmt.limit(_MATCH_FETCH_LIMIT)
        rows = list(session.scalars(stmt).all())
        dicts = [_to_dict(r) for r in rows]
    return rank_buckets(observed_signals, symptom, dicts, top_k=top_k)


def _set_mirror_status(session, row: FailureBucket, status: str) -> None:
    if row.document_id:
        doc = session.get(Document, row.document_id)
        if doc is not None:
            doc.status = status


def retire_bucket(bucket_id: str, *, reason: str, retired_by: Optional[str]) -> dict[str, Any]:
    """Soft-hide a bucket: row kept, hidden from list/match/search/graph/aggregates.

    Search exclusion reuses Document.status='archived' (the mirror document), which
    every search path already filters. Idempotent: an already-retired bucket is
    returned unchanged (original reason/actor preserved).
    """
    reason = (reason or "").strip()
    if len(reason) < MIN_RETIRE_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_RETIRE_REASON_LEN} characters")
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if not row:
            raise KeyError(bucket_id)
        if row.status != STATUS_RETIRED:
            row.status = STATUS_RETIRED
            row.retired_at = datetime.now(timezone.utc)
            row.retired_by = retired_by
            row.retire_reason = reason
            _set_mirror_status(session, row, "archived")
            session.flush()
    result = get_bucket(bucket_id)
    assert result is not None
    return result


def restore_bucket(bucket_id: str) -> dict[str, Any]:
    """Undo retire (or promote a -test row): status=active, mirror searchable again."""
    with session_scope() as session:
        row = session.get(FailureBucket, bucket_id)
        if not row:
            raise KeyError(bucket_id)
        was_active = row.status == STATUS_ACTIVE
        row.status = STATUS_ACTIVE
        row.retired_at = None
        row.retired_by = None
        row.retire_reason = None
        _set_mirror_status(session, row, "active")
        session.flush()
    if not was_active:
        # Re-run the index so a mirror missing its chunks/embeddings is rebuilt;
        # cheap no-op when the content hash is unchanged.
        _index_bucket(bucket_id)
    result = get_bucket(bucket_id)
    assert result is not None
    return result


def retire_batch(
    *, created_by_prefix: str, reason: str, retired_by: Optional[str], dry_run: bool = True
) -> dict[str, Any]:
    """Retire every non-retired bucket whose created_by starts with the prefix."""
    prefix = (created_by_prefix or "").strip()
    if len(prefix) < 3:
        raise ValueError("created_by_prefix must be at least 3 characters")
    reason = (reason or "").strip()
    if len(reason) < MIN_RETIRE_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_RETIRE_REASON_LEN} characters")
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with session_scope() as session:
        ids = [
            r[0]
            for r in session.execute(
                select(FailureBucket.id)
                .where(FailureBucket.created_by.like(like, escape="\\"))
                .where(FailureBucket.status != STATUS_RETIRED)
                .order_by(FailureBucket.created_at)
            ).all()
        ]
    if not dry_run:
        for bid in ids:
            retire_bucket(bid, reason=reason, retired_by=retired_by)
    return {"dry_run": dry_run, "matched": len(ids), "ids": ids, "retired": 0 if dry_run else len(ids)}
