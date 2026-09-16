"""CI-TEC recurring-incident dashboard: query layer over issue_frames.

Reads only — no writes. All data here comes from `IssueFrame.citec_domains`/
`severity_tier` (populated by `app.frames.job.extract_citec_domains`, see
that module and `app.frames.citec_taxonomy`) joined with `Document` for
SWIM's `발생일시(한국)`/`고객사`/`운영부서` (kept in `Document.metadata_`,
never normalized into their own columns — see module docstring rationale
below for why filtering happens in Python instead of JSONB-path SQL).

Design note — Python-side filtering, not JSONB SQL: `발생일시(한국)`/`고객사`/
`운영부서` live in `Document.metadata_` (JSONB) as free-text SWIM header
fields, not indexed columns. Rather than writing brittle dynamic JSONB-path
SQL for every filter/group-by combination the dashboard might ask for, the
citec_domains/severity_tier-filtered candidate set (already narrowed by two
real indexed columns) is fetched once and grouped/filtered in Python. At
today's corpus scale (15,333 incident_reports documents total, meaningfully
smaller after the citec_domains overlap filter) this is fast and simple;
revisit only if usage patterns show otherwise.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.db.models import Document, FailureBucket, IssueFrame
from app.db.session import session_scope
from app.frames.citec_taxonomy import CITEC_DOMAINS

# Which group_by dimensions the flexible query endpoint accepts, and in
# what order a caller must not exceed (max 3 at once — see
# query_recurring_patterns docstring for why).
VALID_GROUP_DIMS = ("domain", "severity_tier", "dept", "customer", "year")
MAX_GROUP_DIMS = 3

_OCCURRED_AT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _parse_occurred_at(meta: dict[str, Any]) -> Optional[date]:
    """Parses SWIM's `발생일시(한국)` header field ("YYYY-MM-DD HH:MM") into
    a date. Returns None if absent/unparseable rather than raising — SWIM
    header formatting has occasional gaps (see app.ingest.adapters'
    parse_incident_report_file), and a document missing this field should
    just be excluded from time-windowed views, not break the whole query."""
    raw = str(meta.get("발생일시(한국)") or "")
    m = _OCCURRED_AT_RE.match(raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _row_dims(*, domain: str, tier: str, meta: dict[str, Any], occurred: Optional[date]) -> dict[str, str]:
    return {
        "domain": domain,
        "severity_tier": tier,
        "dept": str(meta.get("운영부서") or "(미상)"),
        "customer": str(meta.get("고객사") or "(미상)"),
        "year": str(occurred.year) if occurred else "(미상)",
    }


class _Candidate:
    __slots__ = ("document_id", "external_id", "title", "source_uri", "domains", "tier", "meta", "occurred")

    def __init__(self, *, document_id, external_id, title, source_uri, domains, tier, meta, occurred):
        self.document_id = document_id
        self.external_id = external_id
        self.title = title
        self.source_uri = source_uri
        self.domains = domains
        self.tier = tier
        self.meta = meta
        self.occurred = occurred


def _fetch_candidates(
    *,
    domains: Optional[list[str]],
    severity_tiers: Optional[list[str]],
    source_type: str = "incident_reports",
) -> list[_Candidate]:
    with session_scope() as session:
        stmt = (
            select(
                Document.id,
                Document.external_id,
                Document.title,
                Document.source_uri,
                Document.metadata_,
                IssueFrame.citec_domains,
                IssueFrame.severity_tier,
            )
            .join(Document, Document.id == IssueFrame.document_id)
            .where(Document.source_type == source_type)
            .where(Document.status == "active")
            .where(IssueFrame.severity_tier.is_not(None))
        )
        if domains:
            stmt = stmt.where(IssueFrame.citec_domains.overlap(domains))
        if severity_tiers:
            stmt = stmt.where(IssueFrame.severity_tier.in_(severity_tiers))
        rows = session.execute(stmt).all()

    out: list[_Candidate] = []
    for doc_id, ext_id, title, uri, meta, doc_domains, tier in rows:
        meta = meta or {}
        out.append(
            _Candidate(
                document_id=doc_id,
                external_id=ext_id,
                title=title,
                source_uri=uri,
                domains=list(doc_domains or []),
                tier=tier,
                meta=meta,
                occurred=_parse_occurred_at(meta),
            )
        )
    return out


def query_recurring_patterns(
    *,
    group_by: list[str],
    domains: Optional[list[str]] = None,
    severity_tiers: Optional[list[str]] = None,
    dept_contains: Optional[str] = None,
    customer_contains: Optional[str] = None,
    since_days: Optional[int] = None,
    min_count: int = 3,
    limit: int = 50,
    samples_per_group: int = 3,
) -> dict[str, Any]:
    """Group CI-TEC-domain-tagged incident_reports by up to 3 dimensions
    (domain/severity_tier/dept/customer/year), count, and return the top
    `limit` groups meeting `min_count`, each with `samples_per_group`
    representative incidents (most recent first).

    One incident with N citec_domains contributes to N different `domain`
    groups (multi-label by design — see app.frames.citec_taxonomy) — a
    group's `count` is "incidents touching this domain", not "incidents
    whose ONLY domain is this one". This mirrors how the taxonomy was
    validated throughout the citec-kb conversation (e.g. "DB Hang" counted
    under both Database and 성능).

    3-dimension cap: a 4th dimension (e.g. domain×dept×customer×year) tends
    to fragment groups below `min_count` fast at this corpus's current
    scale (see the citec-kb conversation's real validation run — 3
    dimensions already produced usably-sized groups; a 4th wasn't tested
    and isn't assumed to hold up). Ask for at most 3; combine multiple
    calls with different filters instead of one 4-dimension call.
    """
    invalid = [d for d in group_by if d not in VALID_GROUP_DIMS]
    if invalid:
        raise ValueError(f"invalid group_by dimension(s): {invalid} — valid: {VALID_GROUP_DIMS}")
    if not group_by:
        raise ValueError("group_by must have at least one dimension")
    if len(group_by) > MAX_GROUP_DIMS:
        raise ValueError(f"group_by supports at most {MAX_GROUP_DIMS} dimensions, got {len(group_by)}")

    candidates = _fetch_candidates(domains=domains, severity_tiers=severity_tiers)

    since_cutoff: Optional[date] = None
    if since_days is not None:
        since_cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).date()

    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for cand in candidates:
        if since_cutoff is not None and (cand.occurred is None or cand.occurred < since_cutoff):
            continue
        dept = str(cand.meta.get("운영부서") or "(미상)")
        if dept_contains and dept_contains not in dept:
            continue
        customer = str(cand.meta.get("고객사") or "(미상)")
        if customer_contains and customer_contains not in customer:
            continue

        # "domain" group_by fans one incident out to each of its tags;
        # every other dim is single-valued per incident, so the fan-out
        # only happens along the domain axis.
        domain_values = cand.domains if "domain" in group_by else [None]
        for dv in domain_values:
            full_dims = _row_dims(domain=dv or "", tier=cand.tier, meta=cand.meta, occurred=cand.occurred)
            key = tuple(full_dims[d] for d in group_by)
            g = groups.setdefault(
                key,
                {
                    "group": dict(zip(group_by, key)),
                    "count": 0,
                    "customers": set(),
                    "samples": [],
                },
            )
            g["count"] += 1
            g["customers"].add(customer)
            g["samples"].append(cand)

    result_groups = []
    for g in groups.values():
        if g["count"] < min_count:
            continue
        samples_sorted = sorted(
            g["samples"], key=lambda c: c.occurred or date.min, reverse=True
        )[:samples_per_group]
        result_groups.append(
            {
                "group": g["group"],
                "count": g["count"],
                "customer_count": len(g["customers"]),
                "samples": [
                    {
                        "external_id": c.external_id,
                        "title": c.title,
                        "url": c.source_uri,
                        "occurred_at": c.occurred.isoformat() if c.occurred else None,
                    }
                    for c in samples_sorted
                ],
            }
        )

    result_groups.sort(key=lambda g: g["count"], reverse=True)
    truncated = len(result_groups) > limit
    result_groups = result_groups[:limit]

    return {
        "group_by": group_by,
        "filters": {
            "domains": domains,
            "severity_tiers": severity_tiers,
            "dept_contains": dept_contains,
            "customer_contains": customer_contains,
            "since_days": since_days,
            "min_count": min_count,
        },
        "candidate_count": len(candidates),
        "groups": result_groups,
        "truncated": truncated,
    }


# fb_domain (app.failure_buckets — see references/failure-bucket-domains.md)
# is a SEPARATE, smaller vocabulary owned by diagnostic plugins
# (packet-capture-rca/pacemaker-tools/windows-tools), not designed around
# CI-TEC's 11 domains. Only 3 fb_domain values exist as of 2026-09-16:
# network, cluster, windows. Mapping the other 8 CI-TEC domains onto one of
# these would misrepresent unrelated diagnostic scope as "coverage" — so
# only domains with a genuine, documented fb_domain correspondence are
# mapped here; the rest report "정의된 fb_domain 없음" (no fb_domain exists
# for this domain yet) rather than a fabricated 0%-coverage number, per
# references/failure-bucket-domains.md's "새 도메인 추가 절차" (adding one
# requires a PR to that file + app/taxonomy.py, not an assumption here).
_DOMAIN_TO_FB_DOMAIN: dict[str, Optional[str]] = {
    "Network": "network",
    "Windows": "windows",
    # cluster (pacemaker-tools) is Linux HA-clustering specifically
    # (Pacemaker/Corosync), a narrower scope than CI-TEC's general "Linux"
    # domain — flagged as a partial/proxy match, not equivalence.
    "Linux": "cluster",
    "VMware": None,
    "OpenStack": None,
    "Kubernetes": None,
    "Middleware": None,
    "Storage": None,
    "Ceph": None,
    "Database": None,
    "성능": None,
}


def failure_bucket_coverage(
    *,
    since_days: Optional[int] = 730,
    min_count: int = 3,
) -> dict[str, Any]:
    """Per CI-TEC domain: how many recurring-pattern incidents were found
    (via query_recurring_patterns(group_by=["domain"])) vs. how many
    failure_buckets currently exist for the fb_domain it maps to (if any).

    A domain with recurring incidents but zero (or no-fb_domain-defined)
    failure_bucket coverage is exactly the "확인은 됐는데 아직 안 정리된
    반복 패턴" gap the dashboard's failure_bucket-coverage panel exists to
    surface — see docs/CITEC_DASHBOARD_API.md.
    """
    pattern_result = query_recurring_patterns(
        group_by=["domain"], since_days=since_days, min_count=min_count, limit=len(CITEC_DOMAINS),
        samples_per_group=0,
    )
    incident_counts = {g["group"]["domain"]: g["count"] for g in pattern_result["groups"]}

    with session_scope() as session:
        fb_counts: dict[str, int] = {}
        for fb_domain in {v for v in _DOMAIN_TO_FB_DOMAIN.values() if v}:
            fb_counts[fb_domain] = int(
                session.scalar(
                    select(func.count())
                    .select_from(FailureBucket)
                    .where(FailureBucket.fb_domain == fb_domain)
                )
                or 0
            )

    rows = []
    for domain in CITEC_DOMAINS:
        fb_domain = _DOMAIN_TO_FB_DOMAIN.get(domain)
        recurring_count = incident_counts.get(domain, 0)
        rows.append(
            {
                "domain": domain,
                "recurring_incident_count": recurring_count,
                "fb_domain": fb_domain,
                "failure_bucket_count": fb_counts.get(fb_domain, 0) if fb_domain else None,
                "status": (
                    "no_fb_domain_defined" if fb_domain is None
                    else "gap" if recurring_count > 0 and fb_counts.get(fb_domain, 0) == 0
                    else "covered" if fb_counts.get(fb_domain, 0) > 0
                    else "no_recurring_pattern"
                ),
            }
        )
    rows.sort(key=lambda r: r["recurring_incident_count"], reverse=True)
    return {"since_days": since_days, "min_count": min_count, "domains": rows}


SEVERITY_TIER_LEGEND: dict[str, str] = {
    "major": "1~3등급 — 중대장애",
    "minor": "4등급 — 장애는 있었으나 서비스 영향 미미/없음",
    "failover_no_impact": "FO등급 — Failover되어 서비스 영향 없었던 장애",
    "customer_fault": "X등급 — 고객사 귀책 (SDS 책임 아님)",
    "vendor_fault": "N등급 — 벤더 귀책",
    "unknown": "분류 안 됨 (미인식 등급값 또는 공백)",
}


def domain_catalog() -> dict[str, Any]:
    """Static legend for the dashboard UI — domain list + severity tiers."""
    return {
        "domains": list(CITEC_DOMAINS),
        "severity_tiers": SEVERITY_TIER_LEGEND,
        "group_by_dimensions": list(VALID_GROUP_DIMS),
        "max_group_by_dimensions": MAX_GROUP_DIMS,
    }
