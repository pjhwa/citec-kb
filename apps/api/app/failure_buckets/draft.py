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
        # domain is intentionally left unset here: app.taxonomy.infer_domain has a
        # dedicated `source_type == "failure_bucket"` branch (from metadata["protocol"])
        # that the ingest pipeline (enrich_draft_fields) calls via `domain or infer_domain(...)`.
        # Setting a truthy domain here would shadow that branch and duplicate its logic.
        domain=None,
    ).finalize()
