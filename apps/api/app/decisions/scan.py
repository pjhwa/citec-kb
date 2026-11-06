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
            "gap_max_groups_per_domain": 5, "gap_max_incidents": 60, "gap_max_group_size": 8,
            "llm_refine": False, "llm_max_per_run": 20, "llm_workers": 4}

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


def _one_line(v, limit: int = 140) -> str:
    t = " ".join(str(v or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


# 장애원인 칸은 최초 보고 때 쓴 문구("원인파악중")가 닫힌 뒤에도 남는다. 확정된 원인은 아래 칸들에 적힌다.
_CAUSE_FIELDS = ("근본원인", "직접원인", "장애원인 상세내용", "사유", "장애원인")
# 원인 분석이 끝난 단계(그 앞 단계: 장애발생·조치중·조치완료·원인분석중)
_CAUSE_DONE_STATUS = {"원인분석완료", "대책수립중", "대책수립완료", "FRB 준비중", "종료검토", "종료요청", "종료확정"}


def _section(md: dict, body: str, label: str) -> str:
    val = md.get(label)
    if not val:
        m = re.search(r"^■\s*" + re.escape(label) + r"\s*[:：]\s*(.+?)(?=\n■|\Z)", body, re.S | re.M)
        val = m.group(1) if m else ""
    return str(val or "")


def _incident_fields(doc) -> dict:
    """장애 문서의 상황·원인·조치를 한 줄씩 뽑는다.

    원인은 확정 원인 칸(근본원인 → 직접원인 → 장애원인 상세내용 → 사유)을 먼저, 상용구뿐이 아닌 첫 값을 쓰고
    cause_src에 출처 칸을 남긴다. 모두 비면 장애원인 칸(최초 보고 문구)을 쓴다. 직접원인은 따로 direct로 싣는다.
    """
    md = (doc.metadata_ or {}) if doc is not None else {}
    body = (doc.body_md or "") if doc is not None else ""
    out = {"situation": _one_line(_section(md, body, "장애상황")),
           "action": _one_line(_section(md, body, "장애조치")),
           "status": _one_line(md.get("진행상태"), 20), "cause": "", "cause_src": "", "direct": ""}
    for label in _CAUSE_FIELDS:
        val = _section(md, body, label)
        if has_cause({"cause": val}):
            out["cause"], out["cause_src"] = _one_line(val, 220), label
            break
    else:
        out["cause"], out["cause_src"] = _one_line(_section(md, body, "장애원인"), 220), "장애원인"
    if out["cause_src"] != "직접원인":
        direct = _section(md, body, "직접원인")
        if has_cause({"cause": direct}):
            out["direct"] = _one_line(direct, 140)
    return out


# 원인 칸이 "상세원인 파악 중"뿐인 장애는 같은 원인인지 판단할 근거가 없다.
_CAUSE_BOILER = re.compile(r"상세\s*원인|원인\s*(을\s*)?(파악|확인)\s*중|(파악|확인|조사|분석)\s*중|미상|불명")
_NONWORD = re.compile(r"[\W_]+")


def _cause_core(f: dict) -> str:
    """원인 문구에서 '파악 중' 같은 상용구와 기호를 뺀 핵심 글자만."""
    return _NONWORD.sub("", _CAUSE_BOILER.sub("", f.get("cause") or ""))


def has_cause(f: dict) -> bool:
    return len(_cause_core(f)) >= 6


_UNCONFIRMED = re.compile(r"원인\s*불명|원인\s*미상|확정\s*(이\s*)?어렵|(파악|확인|조사|분석)\s*중")


def cause_confirmed(f: dict) -> bool:
    """원인이 확정된 장애인가: 원인 분석 단계를 지났고(진행상태를 알 때), 원인 문구가 있고, 미확정 표현이 없다."""
    st = (f.get("status") or "").strip()
    if st and st not in _CAUSE_DONE_STATUS:
        return False
    return has_cause(f) and not _UNCONFIRMED.search(f.get("cause") or "")


def _grams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def cluster_by_cause(items: list[dict], *, threshold: float = 0.25) -> list[list[dict]]:
    """원인 문구가 비슷한(글자 2-gram Jaccard ≥ threshold, 기본 0.25) 장애끼리 묶는다(연결 요소). 큰 순서로 반환."""
    g = [_grams(_cause_core(i)) for i in items]
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            u = len(g[a] | g[b])
            if u and len(g[a] & g[b]) / u >= threshold:
                parent[find(a)] = find(b)
    groups: dict[int, list[dict]] = {}
    for idx, it in enumerate(items):
        groups.setdefault(find(idx), []).append(it)
    return sorted(groups.values(), key=len, reverse=True)


def _fmt_list(v) -> str:
    return "\n".join(f"- {x}" for x in (v or [])) or "(없음)"


def _bucket_context(b: FailureBucket) -> dict:
    """판정자가 버킷 본문을 따로 열지 않고도 판단할 수 있게 핵심 필드를 그대로 싣는다."""
    return {
        "kind": "failure_bucket", "heading": f"실패 버킷 '{(b.bucket_name or '')[:120]}' 상세",
        "fields": [
            {"label": "증상", "value": b.symptom or "(없음)"},
            {"label": "판별 신호", "value": _fmt_list(b.discriminating_signals)},
            {"label": "반증 신호", "value": _fmt_list(b.counter_signals)},
            {"label": "근본원인", "value": b.root_cause or "(없음)"},
            {"label": "권고 조치", "value": b.recommended_action or "(없음)"},
            {"label": "지지/반증 사례", "value": f"지지 {b.support_count}건 · 반증 {b.counter_count}건 · 기계 신뢰도 {b.confidence}"},
            {"label": "근거 포인터", "value": b.evidence_ref or "(없음)"},
        ],
        "links": [{"label": "버킷 원문 문서", "href": f"/doc.html?id={b.document_id}"}] if b.document_id else [],
    }


def detect_fb_confirm(session) -> list[Proposal]:
    from app.taxonomy import _FB_DOMAIN_TO_CORPUS_DOMAIN

    out: list[Proposal] = []
    rows = session.scalars(select(FailureBucket).where(
        FailureBucket.status == "active", FailureBucket.confidence < FB_CONFIRMED_CONFIDENCE,
        # 지지 사례가 하나도 없는 버킷은 사람이 확정할 근거가 없으므로 올리지 않는다(근거가 쌓이면 대상이 된다).
        FailureBucket.support_count > 0, FailureBucket.document_id.is_not(None)).order_by(FailureBucket.created_at)).all()
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
                        "counter_count": b.counter_count, "context": _bucket_context(b)},
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


def _mark_overlaps(session, proposals: list[Proposal]) -> None:
    """같은 장애가 다른 공백 제안(이번 실행분 + 이미 열린 것)에도 걸려 있으면 항목에 also_in으로 표시한다."""
    seen: dict[str, list[tuple[str, str]]] = {}

    def add(key: str, ctx: dict) -> None:
        for it in ctx.get("items") or []:
            if it.get("document_id"):
                seen.setdefault(it["document_id"], []).append((key, ctx.get("heading") or key))

    for p in proposals:
        add(p.source_key, p.source_ref.get("context") or {})
    for d in session.scalars(select(Decision).where(Decision.status != "retired",
                                                    Decision.source_key.like("gap3:%"))):
        add(d.source_key, (d.source_ref or {}).get("context") or {})
    for p in proposals:
        for it in (p.source_ref.get("context") or {}).get("items") or []:
            others = sorted({h for k, h in seen.get(it.get("document_id"), []) if k != p.source_key})
            if others:
                it["also_in"] = others[:3]


def detect_bucket_gap(session, *, since_days: int = 365, min_count: int = 3, max_groups: int = 5,
                      max_incidents: int = 60, max_group_size: int = 8) -> list[Proposal]:
    """실패 버킷이 없는 도메인의 반복 장애를 기존 반복 장애 그룹(도메인 × 고객사) 단위로 제안한다.

    - 원인이 확정되지 않은 장애(원인 분석 단계 전, 원인 미기록·"파악 중"·원인불명)는 목록에서 뺀다.
    - 한 그룹이 max_group_size를 넘으면 원인 문구가 비슷한 장애끼리 다시 나눠, 묶음마다 질문 하나를 만든다.
      비슷한 원인이 min_count건 미만인 장애는 반복 패턴이 아니므로 제안하지 않는다.
    - 같은 장애가 다른 제안에도 있으면 항목에 also_in으로 표시한다.
    """
    from app.citec_dashboard.service import failure_bucket_coverage, query_recurring_patterns

    out: list[Proposal] = []
    cov = failure_bucket_coverage(since_days=since_days, min_count=min_count)
    for row in cov["domains"]:
        if row["status"] != "gap":
            continue
        pat = query_recurring_patterns(group_by=["domain", "customer"], domains=[row["domain"]], since_days=since_days,
                                       min_count=min_count, limit=200, samples_per_group=max_incidents)
        made = 0
        for g in pat["groups"]:
            customer = str(g["group"].get("customer") or "")
            if g["group"].get("domain") != row["domain"]:
                continue  # 장애 하나가 여러 도메인 태그를 가질 수 있어 다른 도메인 묶음이 섞여 나온다
            if made >= max_groups:
                break
            if not customer or customer == "(미상)":
                continue  # 고객사를 모르는 묶음은 '같은 패턴'이라 볼 근거가 없다
            eids = [str(smp.get("external_id") or "") for smp in g["samples"]]
            docs = {d.external_id: d for d in session.scalars(select(Document).where(
                Document.source_type == "incident_reports",
                Document.external_id.in_(eids + [f"swim_{e}" for e in eids])))}
            incidents = []
            for smp in g["samples"]:
                eid = str(smp.get("external_id") or "")
                doc = docs.get(eid) or docs.get(f"swim_{eid}")
                title = (smp.get("title") or (doc.title if doc else "") or "").strip()
                incidents.append({"id": eid, "title": title, "date": (smp.get("occurred_at") or "")[:10],
                                  "href": f"/doc.html?eid={doc.external_id}&st=incident_reports" if doc else None,
                                  "document_id": doc.id if doc else None, **_incident_fields(doc)})
            kept = [i for i in incidents if cause_confirmed(i)]
            no_cause = len(incidents) - len(kept)
            if len(kept) < min_count:
                continue
            if len(kept) > max_group_size:
                parts = [(f"#{k}", c) for k, c in enumerate(
                    (c for c in cluster_by_cause(kept) if len(c) >= min_count), 1)]
            else:
                parts = [("", kept)]
            for suffix, members in parts:
                if made >= max_groups:
                    break
                evidence = [{"quote": i["title"], "stance": "context", "document_id": i["document_id"]}
                            for i in members if i["document_id"] and i["title"]][:3]
                if not evidence:
                    continue
                n = len(members)
                made += 1
                head = f"{row['domain']} · {customer} · 최근 {since_days}일 장애 {n}건"
                if suffix:
                    head += f" (원인이 비슷한 묶음 {suffix[1:]})"
                notes = ["묶음 기준은 도메인×고객사입니다. 같은 원인인 장애를 체크하세요. 체크한 건이 있으면 그 건들을 근거로 '정리 필요'로 확정됩니다(없으면 '개별 사건')."]
                if suffix:
                    notes.append(f"그룹 전체 {len(incidents)}건 중 원인 문구가 비슷한 건만 모았습니다.")
                if no_cause:
                    notes.append(f"원인이 확정되지 않은 {no_cause}건(원인 분석 중·미기록)은 목록에서 뺐습니다.")
                if g["count"] > len(incidents):
                    notes.append(f"(최대 {max_incidents}건만 표시)")
                out.append(Proposal(
                    detector="bucket_gap", source_key=f"gap3:{row['domain']}:{row['fb_domain']}:{customer}{suffix}",
                    question=f"{row['domain']} 도메인 '{customer}'의 최근 {since_days}일 장애 {n}건 중, 같은 원인으로 실패 버킷에 정리할 건이 있습니까?",
                    options=[
                        {"key": "consolidate", "label": "정리 필요", "summary": "아래 목록에서 같은 원인인 장애를 체크하면 그 건들을 근거로 확정"},
                        {"key": "individual", "label": "개별 사건", "summary": "같은 원인으로 묶을 장애가 없음"},
                    ],
                    evidence=evidence, stakes="mid", topic="failure_bucket 공백", domain=None,
                    source_ref={"type": "domain_gap", "domain": row["domain"], "fb_domain": row["fb_domain"],
                                "customer": customer, "recurring_incident_count": n,
                                "excluded_no_cause": no_cause,
                                "context": {"kind": "incident_group", "heading": head, "selectable": True,
                                            "note": " ".join(notes), "items": members}},
                ))
    _mark_overlaps(session, out)
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
                                                    min_count=int(cfg["gap_min_count"]),
                                                    max_groups=int(cfg["gap_max_groups_per_domain"]),
                                                    max_incidents=int(cfg["gap_max_incidents"]),
                                                    max_group_size=int(cfg["gap_max_group_size"]))),
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
    llm_run = None
    if not dry_run:
        if cfg.get("llm_refine"):  # 1단계: 문구만 다듬는다(근거·선택지 key는 불변). 실패하면 규칙 문구.
            from app.decisions import refine

            llm_run = refine.Run()
            refined = refine.refine_all(todo[: int(cfg["llm_max_per_run"])], run=llm_run,
                                        workers=int(cfg["llm_workers"]))
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

        llm_run = refine.Run()
        previews = refine.preview_all(todo[: int(cfg["llm_max_per_run"])], run=llm_run,
                                      workers=int(cfg["llm_workers"]))
    by_det: dict[str, int] = {}
    for p in found:
        by_det[p.detector] = by_det.get(p.detector, 0) + 1
    return {
        "dry_run": dry_run, "detected": by_det, "already_known": len(found) - len(new),
        "would_create": [{"detector": p.detector, "source_key": p.source_key, "question": p.question, "stakes": p.stakes,
                          "context": p.source_ref.get("context")}
                         for p in todo] if dry_run else None,
        "created": created, "created_count": len(created), "llm_refined": refined, "llm_preview": previews,
        "llm_auth_failed": bool(llm_run and llm_run.auth_failed), "llm_skipped": llm_run.skipped if llm_run else 0,
        "skipped_by_cap": max(0, len(new) - len(todo)), "open_server_proposals_before": open_server,
        "caps": {"max_new_per_run": cap_new, "max_open_server_proposals": cfg["max_open_server_proposals"]},
        "errors": errors,
    }
