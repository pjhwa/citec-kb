"""Fold kb_ask SSE events into one answer. No MCP or HTTP imports."""

from __future__ import annotations


class KbAskError(RuntimeError):
    """Hard failure: no snippet fallback arrived. MCP should set isError."""


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
                if path:
                    sources.append(path)
                cite_lines.append(
                    f"- {c.get('id') or ''} {c.get('title') or eid}\n"
                    f"  path: {path}\n"
                    f"  body_api: {body}\n"
                    f"  web_url: {web}\n"
                    f"  mcp: kb_get_document(path={path!r})"
                )

    if not saw_done:
        detail = errors[-1] if errors else "답변 스트림이 완료되지 않았습니다."
        raise KbAskError(detail)

    answer = "".join(answer_parts).strip()
    if not answer:
        detail = errors[-1] if errors else "답변을 생성하지 못했습니다."
        raise KbAskError(detail)

    if llm_error or errors:
        answer = "[스니펫 요약] LLM 생성 답변이 아닙니다. 검색 근거만 요약합니다.\n" + answer
    if cite_lines:
        answer += "\n\n**출처 (원문 접근)**\n" + "\n".join(cite_lines)
    elif sources:
        answer += "\n\n**출처 path**: " + ", ".join(sources)
        answer += "\n원문: kb_get_document(path=…)"
    return answer
