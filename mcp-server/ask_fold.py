"""Fold kb_ask SSE events into one answer. No MCP or HTTP imports."""

from __future__ import annotations


class KbAskError(RuntimeError):
    """Hard failure: no snippet fallback arrived. MCP should set isError."""


def _decision_line(d: dict | None) -> str:
    """확정 결정 인용에는 확정자·확정일·재확인 여부를 함께 적는다."""
    if not d:
        return ""
    who = f"{d.get('decided_by')}{' (자기 신고)' if d.get('self_reported') else ''}"
    line = f"\n  사람 확정: 확정자 {who}, 확정일 {str(d.get('decided_at') or '')[:10]}"
    if d.get("recheck"):
        line += " — ⚠ 재확인 필요(사실로 단정하지 말고 원문 확인)"
    return line


def fold_ask_events(events: list[dict]) -> str:
    """Return the answer text.

    An ``error`` event is kept, not returned immediately. A later ``done``
    event carries the snippet fallback and is the answer. If the stream
    ends without ``done``, or ``done`` has no text, raise KbAskError.
    """
    answer_parts: list[str] = []
    sources: list[str] = []
    cite_lines: list[str] = []
    errors: list[str] = []
    saw_done = False
    llm_error: str | None = None
    gen_status: str | None = None
    seen_paths: set[str] = set()
    # Every [C#] pointing at the same document, in citation order (A2): the
    # answer may cite C11 while only the document's first chunk C1 was listed.
    ids_by_path: dict[str, list[str]] = {}
    cite_order: list[str] = []
    entries: dict[str, dict] = {}

    for event in events:
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "token":
            answer_parts.append(event.get("text") or "")
        elif etype == "sources":
            sources = list(event.get("files") or [])
        elif etype == "error":
            errors.append(str(event.get("text") or event.get("error") or "알 수 없는 오류"))
        elif etype == "done":
            saw_done = True
            result = event.get("result") or {}
            if result.get("llm_error"):
                llm_error = str(result.get("llm_error"))
            gen_status = result.get("generation_status") or gen_status
            if not answer_parts and result.get("answer"):
                answer_parts.append(str(result.get("answer")))
            for c in result.get("citations") or []:
                if not isinstance(c, dict):
                    continue
                eid = c.get("external_id") or ""
                st = c.get("source_type") or ""
                path = c.get("path") or (f"{st}/{eid}.md" if eid and st else eid)
                web = c.get("web_url") or c.get("web_path") or ""
                body = c.get("body_api") or c.get("body_api_url") or ""
                # One entry per document, but keep every chunk's citation id.
                cid = str(c.get("id") or "")
                ids = ids_by_path.setdefault(path, [])
                if cid and cid not in ids:
                    ids.append(cid)
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                if path:
                    sources.append(path)
                entries[path] = {"title": c.get("title") or eid, "body": body, "web": web,
                                 "decision": c.get("decision")}
                cite_order.append(path)

    if not saw_done:
        detail = errors[-1] if errors else "답변 스트림이 완료되지 않았습니다."
        raise KbAskError(detail)

    answer = "".join(answer_parts).strip()
    if not answer:
        detail = errors[-1] if errors else "답변을 생성하지 못했습니다."
        raise KbAskError(detail)

    if llm_error or errors or (gen_status and gen_status not in {"ok", "not_attempted"}):
        reason = llm_error or (errors[-1] if errors else gen_status)
        head = f"[스니펫 요약] ⚠ 생성 실패 — 검색 스니펫 요약입니다 (LLM 답변 아님; generation_status={gen_status or 'failed_snippet_fallback'}, 사유: {reason})"
        # The API already prefixes its own notice; keep the MCP header as line 1 once.
        answer = head + "\n" + answer.replace("⚠ 생성 실패 — 아래는 LLM 답변이 아니라 검색 스니펫 요약입니다.\n", "", 1)
    for path in cite_order:
        e = entries[path]
        cite_lines.append(
            f"- {', '.join(ids_by_path.get(path) or [])} {e['title']}\n"
            f"  path: {path}\n"
            f"  body_api: {e['body']}\n"
            f"  web_url: {e['web']}\n"
            f"  mcp: kb_get_document(path={path!r})"
            + _decision_line(e.get("decision"))
        )
    if cite_lines:
        answer += "\n\n**출처 (원문 접근)**\n" + "\n".join(cite_lines)
    elif sources:
        answer += "\n\n**출처 path**: " + ", ".join(sources)
        answer += "\n원문: kb_get_document(path=…)"
    return answer
