"""Synthetic query generation for slices the real query log does not cover."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.evalv2 import qfilter as qf
from app.evalv2.llmjson import extract_json

logger = logging.getLogger("citec.evalv2.synth")

MIN_Q, MAX_Q = 5, 120
MAX_OVERLAP = 0.5
EXCERPT_OVERLAP = 0.6
MIN_BODY = 200
MIN_BODY_POINTER = 20

# slice -> source_types to sample from
SLICE_SOURCES: dict[str, tuple[str, ...]] = {
    "pointer": ("confluence_map",),
    "symptom": ("support_history",),
    "vocab": ("tech_repo",),
}

_HINT = {
    "vocab": "문서의 핵심 개념을 가리키되 제목과 반대 언어/표기(제목이 영문·약어면 한글 풀어쓴 표현, 한글이면 영문 용어나 약어)로 "
             "검색어를 만드세요. 제목에 있는 영문 약어는 질의에 쓰지 마세요.",
    "pointer": "이 문서가 안내하는 주제를 찾으려는 현업이 입력할 법한 짧은 검색어를 만드세요.",
    "symptom": "이 문서가 다루는 문제를 겪는 현업이 증상을 서술하는 한 문장 질문을 만드세요.",
}

_STYLE = (
    "당신은 이 문서를 본 적 없는 현업 엔지니어입니다. 검색창에 직접 치는 말투(명사구·증상 나열)로 쓰세요.\n"
    "좋은 예: 서버 재부팅 후 네트워크 안 잡힘 / 디스크 용량 경고 계속 뜸\n"
    "나쁜 예: 문서에 적힌 문장을 그대로 옮긴 긴 문장 / 제목 단어를 나열한 질의\n"
)


def id_prefix(slice_name: str, seed: str) -> str:
    return f"s-{slice_name}-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:4]}"


def build_messages(slice_name: str, doc: dict[str, Any], n_queries: int = 2) -> list[dict[str, str]]:
    if slice_name not in _HINT:
        raise ValueError(f"unknown slice: {slice_name}")
    content = (
        "사내 기술지원 지식베이스의 검색 평가용 질의를 만듭니다.\n"
        f"아래 문서를 찾으려는 사람이 입력할 검색 질의 {n_queries}개를 만드세요.\n"
        f"{_HINT[slice_name]}\n"
        f"{_STYLE}"
        "문서 제목의 단어를 그대로 쓰지 마세요. 발췌 문장의 단어도 베끼지 마세요. 고객명·사람 이름·IP·계정은 쓰지 마세요.\n"
        '반드시 JSON으로만 답하세요: {"queries":["…","…"]}\n\n'
        f"문서 제목: {doc['title']}\n문서 발췌: {doc.get('excerpt', '')}"
    )
    return [{"role": "user", "content": content}]


def parse_queries(raw: str | None) -> list[str]:
    data = extract_json(raw)
    qs = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(qs, list):
        return []
    return [" ".join(q.split()) for q in qs if isinstance(q, str) and q.strip()]


def accept_queries(
    queries: list[str], title: str, *, known: set[str], seen: set[str], excerpt: str | None = None
) -> list[str]:
    out: list[str] = []
    for q in queries:
        n = qf.norm(q)
        if not (MIN_Q <= len(q) <= MAX_Q) or n in known or n in seen:
            continue
        if qf.too_easy(q, title, max_ratio=MAX_OVERLAP):
            continue
        if excerpt and qf.title_overlap(q, excerpt[:200]) > EXCERPT_OVERLAP:
            continue
        if qf.sensitive_hints(q):
            continue
        seen.add(n)
        out.append(q)
    return out


def sample_docs(session: Session, slice_name: str, n: int, *, seed: str, prefix: str = "") -> list[dict[str, Any]]:
    """Deterministic pseudo-random sample (md5 of external_id + seed) of active docs for a slice.

    Oversamples (limit 2n) so generate(want_docs=n) can skip docs the LLM/filters reject.
    """
    from app.db.models import Document

    sources = SLICE_SOURCES[slice_name]
    min_body = MIN_BODY_POINTER if slice_name == "pointer" else MIN_BODY
    stmt = (
        select(Document.external_id, Document.title, Document.body_md, Document.source_type)
        .where(Document.status == "active", Document.source_type.in_(sources))
        .where(func.length(Document.title) >= 8)
        .where(func.length(func.coalesce(Document.body_md, "")) >= min_body)
        .where(Document.external_id.startswith(prefix, autoescape=True))
    )
    if slice_name == "vocab":
        stmt = stmt.where(
            (Document.title + " " + func.left(func.coalesce(Document.body_md, ""), 600)).op("~")("[A-Za-z]{2,}")
        )
    stmt = stmt.order_by(func.md5(Document.external_id + seed)).limit(n * 2)
    rows = session.execute(stmt).all()
    return [
        {"external_id": r.external_id, "title": r.title, "source_type": r.source_type,
         "excerpt": " ".join((r.body_md or "").split())[:600]}
        for r in rows
    ]


def generate(
    chat: Callable[..., str], slice_name: str, docs: list[dict[str, Any]], *,
    known: set[str], id_prefix: str, max_per_doc: int = 2, want_docs: int | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    yielded = 0
    for doc in docs:
        if want_docs is not None and yielded >= want_docs:
            break
        try:
            raw = chat(build_messages(slice_name, doc, max_per_doc), max_tokens=800, temperature=0.7, timeout=120.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth LLM failure for %s: %s", doc.get("external_id"), exc)
            continue
        accepted = accept_queries(
            parse_queries(raw), doc["title"], known=known, seen=seen, excerpt=doc.get("excerpt")
        )[:max_per_doc]
        yielded += bool(accepted)
        for q in accepted:
            items.append({
                "id": f"{id_prefix}-{len(items) + 1:03d}", "q": q, "slice": slice_name, "source": "synthetic",
                "seed_doc": {k: doc.get(k) for k in ("external_id", "title", "source_type")},
            })
    return items
