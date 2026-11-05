"""Periodic topic scan: derive questions a human should confirm and register them in the queue.

Rule-based detection (the LLM never decides which topics exist). Optional `llm_refine` rewords the
question/options of proposals about to be created (decisions/refine.py). Every proposal carries a
`source_key` (unique) so a re-run never re-proposes a topic, and verbatim quotes from the
pinned source so the same evidence gate as any proposal applies. Proposals are only *proposals*
(status=proposed, created_by_kind=server_job); a human decides in /decisions.html.

Detectors
  fb_confirm  active failure buckets whose confidence is still below FB_CONFIRMED_CONFIDENCE:
              "is this a confirmed failure pattern?" (confirm / revise / retire).
  deadline    tech_repo/confluence_docs pages whose TITLE states a date deadline ("…2026.11.30 까지 제공"):
              "is the deadline in the title the current standard?"
  bucket_gap  CI-TEC domains with recurring incidents but zero active failure buckets
              (citec_dashboard.failure_bucket_coverage status=gap): "should this be consolidated?"

Queue-flood guard: at most `max_new_per_run` new proposals per run and no new ones while
`max_open_server_proposals` server proposals are still waiting (fatigue control, spec §3-6).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import Decision, DecisionEvidence, Document, FailureBucket
from app.db.session import session_scope
from app.decisions import service as svc
from app.settings import get_settings

FB_CONFIRMED_CONFIDENCE = 0.7
SCAN_ACTOR = "decision-scan"
DEFAULTS = {"max_new_per_run": 20, "max_open_server_proposals": 60, "detectors": ["fb_confirm", "deadline", "bucket_gap"],
            "gap_since_days": 365, "gap_min_count": 3, "deadline_soon_days": 60,
            "llm_refine": False, "llm_max_per_run": 20}

_DEADLINE_RE = re.compile(
    r"((?:19|20)\d{2})[.\-/]\s?0?(\d{1,2})[.\-/]\s?0?(\d{1,2})\s*(?:일)?\s*(까지|이후|종료|제공|사용|지원|만료)")
_FB_TEST_SUFFIX = re.compile(r"-test(\b|$)", re.I)
# 스모크/시험으로 등록된 버킷은 사람이 확정할 주제가 아니다.
_FB_FIXTURE_NAME = re.compile(r"^(SMOKE|TEST|E2E)[-_ ]", re.I)


@dataclass
class Proposal:
    detector: str
    source_key: str
    question: str
    options: list[dict]
    evidence: list[dict] = field(default_factory=list)
    stakes: str = "mid"
    topic: Optional[str] = None
    domain: Optional[str] = None
    source_ref: dict = field(default_factory=dict)


def load_config() -> dict[str, Any]:
    from app.decisions import scan_config

    return scan_config.effective(DEFAULTS)


# ---------------------------------------------------------------- detectors

def _line_with(prefix: str, body: str) -> Optional[str]:
    for ln in (body or "").splitlines():
        if ln.startswith(prefix) and len(ln.strip()) > len(prefix) + 1 and "(미상)" not in ln:
            return ln.strip()[:300]
    return None


def detect_fb_confirm(session) -> list[Proposal]:
    from app.taxonomy import _FB_DOMAIN_TO_CORPUS_DOMAIN

    out: list[Proposal] = []
    rows = session.scalars(select(FailureBucket).where(
        FailureBucket.status == "active", FailureBucket.confidence < FB_CONFIRMED_CONFIDENCE,
        FailureBucket.document_id.is_not(None)).order_by(FailureBucket.created_at)).all()
    for b in rows:
        if _FB_TEST_SUFFIX.search(b.created_by or "") or _FB_FIXTURE_NAME.search(b.bucket_name or ""):
            continue
        doc = session.get(Document, b.document_id)
        if doc is None:
            continue
        # 미러 본문의 줄을 글자 그대로 인용한다(서버 인용 검증을 통과해야 한다).
        quote = _line_with("근본원인:", doc.body_md) or _line_with("증상:", doc.body_md)
        if not quote:
            continue
        name = (b.bucket_name or "")[:120]
        out.append(Proposal(
            detector="fb_confirm", source_key=f"fb:{b.id}",
            question=f"실패 버킷 '{name}' ({b.fb_domain})을(를) 확정된 장애 패턴으로 인정할까요?",
            options=[
                {"key": "confirm", "label": "확정 패턴으로 인정", "summary": "판별 신호·근본원인이 맞다"},
                {"key": "revise", "label": "보완 필요", "summary": "신호나 원인을 고친 뒤 다시 확인"},
                {"key": "retire", "label": "패턴 아님(폐기)", "summary": "버킷을 retire 대상으로 본다"},
            ],
            evidence=[{"quote": quote, "stance": "context", "document_id": doc.id}],
            stakes="mid", topic="failure_bucket", domain=_FB_DOMAIN_TO_CORPUS_DOMAIN.get(b.fb_domain),
            source_ref={"type": "failure_bucket", "id": b.id, "fb_domain": b.fb_domain,
                        "confidence": b.confidence, "support_count": b.support_count,
                        "counter_count": b.counter_count},
        ))
    return out


def detect_deadline(session, *, today: Optional[date] = None, soon_days: int = 60) -> list[Proposal]:
    today = today or date.today()
    out: list[Proposal] = []
    rows = session.execute(select(Document.id, Document.title, Document.source_type, Document.domain).where(
        Document.status == "active", Document.source_type.in_(("tech_repo", "confluence_docs")),
        Document.title.op("~")(r"(19|20)[0-9]{2}[.\-/] ?[0-9]{1,2}[.\-/] ?[0-9]{1,2} *(일)? *(까지|이후|종료|제공|사용|지원|만료)"),
    ).order_by(Document.updated_at.desc())).all()
    for r in rows:
        m = _DEADLINE_RE.search(r.title or "")
        if not m:
            continue
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            due = date(y, mo, d)
        except ValueError:
            continue
        label = f"{y}.{mo:02d}.{d:02d}"
        subject = re.sub(r"\s*[_\-–—]?\s*" + re.escape(m.group(0)), "", r.title).strip(" _-–—") or r.title
        soon = (due - today).days <= soon_days  # 이미 지났거나 임박
        out.append(Proposal(
            detector="deadline", source_key=f"deadline:{r.id}",
            question=f"'{subject[:150]}'의 기한은 제목대로 {label}입니까?",
            options=[
                {"key": "as_titled", "label": label, "summary": "제목에 적힌 기한이 현재 기준"},
                {"key": "changed", "label": "연장·변경됨", "summary": "기한이 달라졌다"},
            ],
            evidence=[{"quote": r.title, "stance": "supports", "option_key": "as_titled", "document_id": r.id}],
            stakes="high" if soon else "mid", topic="기한·종료 일정", domain=r.domain,
            source_ref={"type": "document", "document_id": r.id, "source_type": r.source_type,
                        "deadline": label, "due_passed_or_soon": soon},
        ))
    return out


def detect_bucket_gap(session, *, since_days: int = 365, min_count: int = 3) -> list[Proposal]:
    from app.citec_dashboard.service import failure_bucket_coverage, query_recurring_patterns

    out: list[Proposal] = []
    cov = failure_bucket_coverage(since_days=since_days, min_count=min_count)
    for row in cov["domains"]:
        if row["status"] != "gap":
            continue
        pat = query_recurring_patterns(group_by=["domain"], domains=[row["domain"]], since_days=since_days,
                                       min_count=min_count, limit=1, samples_per_group=3)
        samples = (pat["groups"][0]["samples"] if pat["groups"] else [])
        evidence = []
        for smp in samples:
            eid = str(smp.get("external_id") or "")
            doc = session.scalar(select(Document).where(
                Document.source_type == "incident_reports", Document.external_id.in_((eid, f"swim_{eid}"))))
            if doc and (doc.title or "").strip():
                evidence.append({"quote": doc.title.strip(), "stance": "context", "document_id": doc.id})
        if not evidence:
            continue
        out.append(Proposal(
            detector="bucket_gap", source_key=f"gap:{row['domain']}:{row['fb_domain']}",
            question=f"{row['domain']} 도메인의 반복 장애 {row['recurring_incident_count']}건(최근 {since_days}일)을 실패 버킷으로 정리해야 합니까?",
            options=[
                {"key": "consolidate", "label": "정리 필요", "summary": "반복 패턴을 failure bucket으로 등록"},
                {"key": "individual", "label": "개별 사건", "summary": "패턴으로 묶을 필요 없음"},
            ],
            evidence=evidence, stakes="mid", topic="failure_bucket 공백", domain=None,
            source_ref={"type": "domain_gap", "domain": row["domain"], "fb_domain": row["fb_domain"],
                        "recurring_incident_count": row["recurring_incident_count"]},
        ))
    return out


# ---------------------------------------------------------------- runner

def _documents_with_open_decisions(session) -> set[str]:
    """이미 진행 중(보류 제외) 결정에 근거로 걸려 있는 문서 — 같은 문서로 중복 제안하지 않는다."""
    return set(session.scalars(
        select(DecisionEvidence.document_id).join(Decision, Decision.id == DecisionEvidence.decision_id)
        .where(Decision.status != "retired", DecisionEvidence.document_id.is_not(None))).all())


def scan_topics(*, dry_run: bool = False, max_new: Optional[int] = None, llm_preview: bool = False) -> dict[str, Any]:
    cfg = load_config()
    cap_new = int(max_new if max_new is not None else cfg["max_new_per_run"])
    with session_scope() as s:
        open_server = int(s.scalar(select(func.count()).select_from(Decision).where(
            Decision.created_by_kind == "server_job", Decision.status == "proposed")) or 0)
        known = set(s.scalars(select(Decision.source_key).where(Decision.source_key.is_not(None))).all())
        busy_docs = _documents_with_open_decisions(s)
        found: list[Proposal] = []
        errors: dict[str, str] = {}
        det = cfg["detectors"]
        for name, fn in (
            ("fb_confirm", lambda: detect_fb_confirm(s)),
            ("deadline", lambda: detect_deadline(s, soon_days=int(cfg["deadline_soon_days"]))),
            ("bucket_gap", lambda: detect_bucket_gap(s, since_days=int(cfg["gap_since_days"]),
                                                    min_count=int(cfg["gap_min_count"]))),
        ):
            if name not in det:
                continue
            try:
                found += fn()
            except Exception as exc:  # noqa: BLE001 — 한 탐지기 오류가 나머지를 막지 않는다
                errors[name] = f"{type(exc).__name__}: {exc}"
    new = [p for p in found if p.source_key not in known
           and not any(e["document_id"] in busy_docs for e in p.evidence if p.detector == "deadline")]
    room = max(0, int(cfg["max_open_server_proposals"]) - open_server)
    limit = min(cap_new, room)
    # 위험한 것부터(기한 임박·지남 → 버킷 공백 → 버킷 확정) 등록해 상한에 걸려도 중요한 것이 먼저 올라간다.
    rank = {"deadline": 0, "bucket_gap": 1, "fb_confirm": 2}
    new.sort(key=lambda p: (0 if p.stakes == "high" else 1, rank[p.detector]))
    todo = new[:limit]
    created: list[dict[str, Any]] = []
    refined = 0
    if not dry_run:
        if cfg.get("llm_refine"):  # 1단계: 문구만 다듬는다(근거·선택지 key는 불변). 실패하면 규칙 문구.
            from app.decisions import refine

            for p in todo[: int(cfg["llm_max_per_run"])]:
                refined += int(refine.refine(p))
        for p in todo:
            try:
                d = svc.create_decision(
                    question=p.question, options=p.options, topic=p.topic, domain=p.domain, stakes=p.stakes,
                    created_by_kind="server_job", created_by=f"{SCAN_ACTOR}@{p.detector}", evidence=p.evidence,
                    source_key=p.source_key, source_ref=p.source_ref)
                created.append({"id": d["id"], "detector": p.detector, "source_key": p.source_key})
            except IntegrityError:
                continue  # 동시 실행으로 같은 source_key가 먼저 들어감
            except ValueError as exc:  # 근거 검증 실패 등 — 건너뛰고 보고
                errors[p.source_key] = str(exc)
    previews = None
    if dry_run and llm_preview:  # DB에 쓰지 않고 LLM이 다듬을 문구만 미리 본다(llm_refine 설정과 무관)
        from app.decisions import refine

        previews = [refine.preview(p) for p in todo[: int(cfg["llm_max_per_run"])]]
    by_det: dict[str, int] = {}
    for p in found:
        by_det[p.detector] = by_det.get(p.detector, 0) + 1
    return {
        "dry_run": dry_run, "detected": by_det, "already_known": len(found) - len(new),
        "would_create": [{"detector": p.detector, "source_key": p.source_key, "question": p.question, "stakes": p.stakes}
                         for p in todo] if dry_run else None,
        "created": created, "created_count": len(created), "llm_refined": refined, "llm_preview": previews,
        "skipped_by_cap": max(0, len(new) - len(todo)), "open_server_proposals_before": open_server,
        "caps": {"max_new_per_run": cap_new, "max_open_server_proposals": cfg["max_open_server_proposals"]},
        "errors": errors,
    }
