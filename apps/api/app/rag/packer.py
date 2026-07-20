"""Context packer: fit retrieved chunks into a token budget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass
class PackedChunk:
    cite_id: str  # C1, C2, …
    document_id: str
    chunk_id: str
    title: str
    external_id: str
    source_type: str
    snippet: str
    source_uri: str | None
    score: float
    est_tokens: int


def estimate_tokens(text: str) -> int:
    """Conservative estimate for mixed KO/EN (chars/2, min 1)."""
    n = len(text or "")
    return max(1, (n + 1) // 2)


def pack_chunks(
    hits: Sequence[object],
    *,
    max_context_tokens: int = 12_000,
    per_chunk_chars: int = 1200,
) -> list[PackedChunk]:
    """Pack search hits as cited context blocks under a budget.

    ``hits`` are SearchHit-like objects with attributes used below.
    """
    budget = max(500, max_context_tokens)
    packed: list[PackedChunk] = []
    used = 0
    for i, h in enumerate(hits, start=1):
        title = str(getattr(h, "title", "") or "")
        snip = str(getattr(h, "snippet", "") or "")[:per_chunk_chars]
        body = f"[{title}]\n{snip}".strip()
        est = estimate_tokens(body) + 16
        if used + est > budget and packed:
            break
        cite = f"C{i}"
        packed.append(
            PackedChunk(
                cite_id=cite,
                document_id=str(getattr(h, "document_id", "") or ""),
                chunk_id=str(getattr(h, "chunk_id", "") or ""),
                title=title,
                external_id=str(getattr(h, "external_id", "") or ""),
                source_type=str(getattr(h, "source_type", "") or ""),
                snippet=snip,
                source_uri=getattr(h, "source_uri", None),
                score=float(getattr(h, "score", 0.0) or 0.0),
                est_tokens=est,
            )
        )
        used += est
    return packed


def format_context_block(packed: Sequence[PackedChunk]) -> str:
    parts: list[str] = []
    for p in packed:
        parts.append(
            f"[{p.cite_id}] title={p.title}\n"
            f"source={p.source_type} id={p.external_id}\n"
            f"{p.snippet}"
        )
    return "\n\n---\n\n".join(parts)
