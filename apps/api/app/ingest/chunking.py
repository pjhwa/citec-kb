"""Structure-aware light chunking for PR-03 (no embeddings yet)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.M)


@dataclass
class ChunkDraft:
    ordinal: int
    text: str
    header_context: str
    section_path: str
    token_count: int


def _approx_tokens(s: str) -> int:
    # rough: Korean ~1.5 chars/token; use char/2 as safe upper bound
    return max(1, len(s) // 2)


def chunk_markdown(
    body: str,
    *,
    doc_header: str,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[ChunkDraft]:
    text = (body or "").strip()
    if not text:
        return []

    # Split by headings first
    parts: list[tuple[str, str]] = []  # (path, content)
    matches = list(_HEADING.finditer(text))
    if not matches:
        parts.append(("", text))
    else:
        if matches[0].start() > 0:
            parts.append(("", text[: matches[0].start()].strip()))
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            path = m.group(2).strip()
            content = text[start:end].strip()
            if content:
                parts.append((path, content))

    drafts: list[ChunkDraft] = []
    ordinal = 0
    for path, content in parts:
        pieces = _split_size(content, max_tokens, overlap_tokens)
        for piece in pieces:
            header = doc_header
            if path:
                header = f"{doc_header} | {path}"
            drafts.append(
                ChunkDraft(
                    ordinal=ordinal,
                    text=piece,
                    header_context=header,
                    section_path=path,
                    token_count=_approx_tokens(piece),
                )
            )
            ordinal += 1
    return drafts


def _split_size(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    if _approx_tokens(text) <= max_tokens:
        return [text]
    # paragraph-ish split
    paras = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    buf: list[str] = []
    buf_tok = 0
    for p in paras:
        pt = _approx_tokens(p)
        if buf and buf_tok + pt > max_tokens:
            chunks.append("\n\n".join(buf))
            # overlap: keep tail
            overlap = []
            ot = 0
            for x in reversed(buf):
                xt = _approx_tokens(x)
                if ot + xt > overlap_tokens:
                    break
                overlap.insert(0, x)
                ot += xt
            buf = overlap + [p]
            buf_tok = sum(_approx_tokens(x) for x in buf)
        else:
            buf.append(p)
            buf_tok += pt
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks or [text]
