"""LLM wording pass for rule-detected scan proposals (stage 1).

The rules decide WHICH topics are proposed, what the option keys mean and which verbatim quotes back
them. The LLM may only reword the question and the option labels/summaries. It never adds, removes or
re-keys an option, never touches evidence, and a failed or suspicious answer falls back to the rule
text. Off by default (`llm_refine` in decision_scan.json) and skipped in dry runs.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.decisions import rules

logger = logging.getLogger("citec.decisions.refine")

_NUM = re.compile(r"\d+(?:[.\-/]\d+)*")
_SYSTEM = (
    "당신은 사내 지식베이스의 '사람 확정' 질문을 다듬는 편집자입니다. 규칙이 만든 초안 질문과 선택지를, "
    "사람이 한 번 읽고 판정할 수 있게 명확한 한국어로 고쳐 쓰세요.\n"
    "규칙:\n"
    "- 선택지 key는 그대로 두고 개수도 바꾸지 마세요. label과 summary, 질문 문장만 고칩니다.\n"
    "- 질문 하나에는 한 가지만 묻습니다. 근거에 없는 사실·숫자·날짜를 만들지 마세요.\n"
    "- 질문은 300자 이내, label은 짧게, summary는 한 문장으로 쓰세요.\n"
    "- 선택지는 서로 배타적이어야 합니다.\n"
    '- JSON만 출력하세요: {"question": "...", "options": [{"key": "...", "label": "...", "summary": "..."}]}'
)


def _numbers(text: str) -> set[str]:
    return set(_NUM.findall(text or ""))


def _prompt(p) -> str:
    quotes = "\n".join(f"- {e['quote']}" for e in p.evidence[:5])
    opts = "\n".join(f"- key={o['key']} | {o['label']} | {o['summary']}" for o in p.options)
    return (f"[탐지 유형] {p.detector}\n[초안 질문] {p.question}\n[초안 선택지]\n{opts}\n"
            f"[근거 인용(원문)]\n{quotes}\n[메모] {json.dumps(p.source_ref, ensure_ascii=False)[:400]}")


def _parse(raw: str) -> Optional[dict[str, Any]]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def validate(p, data: dict[str, Any]) -> Optional[tuple[str, list[dict]]]:
    """Return (question, options) when the LLM answer is safe to use, else None."""
    q = rules.norm_text(data.get("question"))
    got = data.get("options")
    if not q or not isinstance(got, list):
        return None
    if [str(o.get("key")) for o in got if isinstance(o, dict)] != [o["key"] for o in p.options]:
        return None  # key set / order must be unchanged
    # Numbers and dates of the rule draft must survive; no new ones may appear.
    if _numbers(q) != _numbers(p.question):
        return None
    opts = []
    for orig, o in zip(p.options, got):
        label, summary = rules.norm_text(o.get("label")), rules.norm_text(o.get("summary"))
        if not label or _numbers(label) != _numbers(orig["label"]):
            return None
        opts.append({"key": orig["key"], "label": label, "summary": summary or orig["summary"]})
    try:
        rules.lint_options(q, opts)
    except ValueError:
        return None
    return q, opts


def refine(p, *, complete=None) -> bool:
    """Reword proposal `p` in place. Returns True when the LLM text was adopted."""
    if complete is None:
        from app.llm_chat import chat_complete as complete
    try:
        raw = complete([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": _prompt(p)}],
                       max_tokens=700, temperature=0.2, timeout=60.0)
    except Exception as exc:  # noqa: BLE001 — LLM 장애는 규칙 문구로 대체
        logger.warning("decision refine skipped (%s): %s", p.source_key, exc)
        return False
    ok = validate(p, _parse(raw) or {})
    if ok is None:
        logger.info("decision refine rejected (%s)", p.source_key)
        return False
    p.source_ref = {**p.source_ref, "llm_refined": True, "rule_question": p.question,
                    "rule_options": p.options}
    p.question, p.options = ok
    return True
