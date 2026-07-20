"""Rule-based issue frame extraction from support_history markdown.

Prefers structured LLM 요약 / h3 sections; falls back to labeled lines and
original body. No LLM call required for v1.
"""

from __future__ import annotations

import re
from typing import Any, Optional


_SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "symptom",
        re.compile(
            r"(?:^|\n)\s*(?:#{1,4}\s*|h[1-3]\.\s*|\*\*)?(?:이슈\s*)?(?:증상|요청이슈|현상|발생\s*현상)"
            r"|(?:^|\n)\s*[-*•]\s*\*\*(?:배경|요청이슈)\*\*",
            re.I,
        ),
    ),
    (
        "root_cause",
        re.compile(
            r"(?:^|\n)\s*(?:#{1,4}\s*|h[1-6]\.\s*|\*\*)?(?:이슈\s*)?(?:장애\s*)?(?:원인|근본\s*원인|분석결과|세부원인)"
            r"|(?:^|\n)\s*[-*•]\s*\*\*(?:분석결과|원인)\*\*"
            r"|(?:^|\n)\s*[-*]?\s*\d+[.)]\s*(?:장애\s*)?원인"
            r"|(?:^|\n)\s*○\s*장애\s*원인"
            r"|(?:^|\n)\s*h[1-6]\.\s*(?:\d+\.?\s*)?(?:장애\s*)?원인"
            r"|(?:^|\n)\s*\.?\s*원인\s*및\s*해결",
            re.I,
        ),
    ),
    (
        "resolution",
        re.compile(
            r"(?:^|\n)\s*(?:#{1,4}\s*|h[1-6]\.\s*|\*\*)?(?:해결(?:\s*방안)?|조치(?:\s*내용|\s*내역)?|장애\s*조치)"
            r"|(?:^|\n)\s*h[1-6]\.\s*(?:\d+\.?\s*)?조치\s*(?:내용|내역)?"
            r"|(?:^|\n)\s*[-*•]\s*\*\*조치내용\*\*"
            r"|(?:^|\n)\s*\*?\s*조치\s*내용\*?"
            r"|(?:^|\n)\s*○\s*(?:장애\s*)?조치"
            r"|(?:^|\n)\s*[-*]?\s*\d+[.)]\s*조치\s*(?:내용|내역)"
            r"|(?:^|\n)\s*조치\s*완료\s*[:：]",
            re.I,
        ),
    ),
    (
        "workaround",
        re.compile(
            r"(?:^|\n)\s*(?:#{1,4}\s*|h[1-3]\.\s*)?(?:임시\s*조치|워크어라운드|workaround)",
            re.I,
        ),
    ),
]

# Inline "label : value" captures (single or few lines)
_INLINE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "root_cause",
        re.compile(
            r"(?:^|\n)\s*(?:[-*•○]?\s*)?(?:\d+[.)]\s*)?(?:장애\s*)?원인\s*[:：]\s*(.+?)(?=\n\s*(?:[-*•○#hH]|\d+[.)]|\Z))",
            re.I | re.S,
        ),
    ),
    (
        "resolution",
        re.compile(
            r"(?:^|\n)\s*(?:[-*•○]?\s*)?(?:장애\s*)?조치(?:\s*내용|\s*내역|\s*완료)?\s*[:：]\s*(.+?)(?=\n\s*(?:[-*•○#hH]|\d+[.)]|\Z))",
            re.I | re.S,
        ),
    ),
    (
        "resolution",
        re.compile(
            r"(?:^|\n)\s*(?:[-*•○]?\s*)?해결(?:\s*방안|\s*책)?\s*[:：]\s*(.+?)(?=\n\s*(?:[-*•○#hH]|\d+[.)]|\Z))",
            re.I | re.S,
        ),
    ),
    (
        "root_cause",
        re.compile(
            r"(?:^|\n)\s*(?:[-*•○]?\s*)?(?:\d+[.)]\s*)?장애원인\s*[:：]?\s*(.+?)(?=\n\s*(?:[-*•○#hH]|\d+[.)]|\Z))",
            re.I | re.S,
        ),
    ),
    (
        "resolution",
        re.compile(
            r"(?:^|\n)\s*\*조치\s*내용\*\s*[:：]?\s*(.+?)(?=\n\s*(?:[-*•○#*]|\d+[.)]|\Z))",
            re.I | re.S,
        ),
    ),
    (
        "symptom",
        re.compile(
            r"(?:^|\n)\s*(?:[-*•○]?\s*)?(?:요청이슈|증상|현상)\s*[:：]\s*(.+?)(?=\n\s*(?:[-*•○#hH*]|\d+[.)]|\Z))",
            re.I | re.S,
        ),
    ),
]

_COMPONENT_HINTS = [
    (re.compile(r"\bRedis\b", re.I), "Redis"),
    (re.compile(r"\bKubernetes\b|\bk8s\b|POD", re.I), "Kubernetes"),
    (re.compile(r"\bESXi\b", re.I), "ESXi"),
    (re.compile(r"\bBFD\b", re.I), "BFD"),
    (re.compile(r"\bOracle\b", re.I), "Oracle"),
    (re.compile(r"\bPostgreSQL\b|\bPostgres\b", re.I), "PostgreSQL"),
    (re.compile(r"\bMySQL\b", re.I), "MySQL"),
    (re.compile(r"\bNetwork\b|네트워크|Spine|BGW|방화벽", re.I), "Network"),
    (re.compile(r"\bStorage\b|스토리지|WEKA|Ceph|NetApp", re.I), "Storage"),
    (re.compile(r"\bLinux\b|리눅스", re.I), "Linux"),
    (re.compile(r"모니모", re.I), "monimo"),
    (re.compile(r"\bSCP\b", re.I), "SCP"),
    (re.compile(r"\bNFS\b", re.I), "NFS"),
    (re.compile(r"\bHA\b|클러스터|Cluster", re.I), "Cluster"),
    (re.compile(r"\bVMware\b|vSphere|v-?motion", re.I), "VMware"),
    (re.compile(r"\bBGP\b|\bCDN\b", re.I), "Network"),
]

_CMD_RE = re.compile(
    r"(?:^|\n)\s*(?:\$|#)\s*([a-zA-Z0-9_./\-]+[^\n]{0,120})",
)

# Timeline style: "... -> 해결O" as weak resolution signal
_RESOLVED_LINE = re.compile(
    r"^(.+?(?:재기동|reboot|리부팅|재시작|적용|변경|조치|패치|업그레이드).+?)\s*->\s*해결\s*[Oo0]",
    re.I | re.M,
)

_PLACEHOLDER_CAUSE = re.compile(
    r"^(세부\s*원인\s*분석\s*예정|상세\s*원인\s*파악\s*중|원인\s*분석\s*중|미상|N/?A|-)\s*$",
    re.I,
)


def _clean_slot_text(s: str) -> str:
    t = s.strip()
    # leftovers from header splits: "내용** : …", "h3. 이슈 원인 * …"
    t = re.sub(r"^(?:조치)?내용\*\*\s*[:：]?\s*", "", t)
    t = re.sub(r"^조치내용\s*[:：]?\s*", "", t)
    t = re.sub(r"^(?:\*\*|내용)\s*[:：]?\s*", "", t)
    t = re.sub(r"^h[1-3]\.\s*[^\n*]{0,40}\*?\s*", "", t, flags=re.I)
    t = re.sub(r"^(?:이슈\s*)?(?:원인|증상|조치|해결(?:\s*방안)?)\s*[:：]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" \t\r\n-•*")
    return t


def _clip(s: Optional[str], n: int = 1200) -> Optional[str]:
    if not s:
        return None
    t = _clean_slot_text(s)
    if not t or len(t) < 2:
        return None
    return t[:n]


def _is_placeholder(s: Optional[str]) -> bool:
    if not s:
        return True
    return bool(_PLACEHOLDER_CAUSE.match(s.strip()))


def _section_body(text: str, start: int, next_starts: list[int]) -> str:
    end = len(text)
    for ns in next_starts:
        if ns > start:
            end = ns
            break
    return text[start:end]


def _better(new: Optional[str], old: Optional[str]) -> bool:
    """Prefer longer, non-placeholder content."""
    if not new or _is_placeholder(new):
        return False
    if not old:
        return True
    if _is_placeholder(old) and not _is_placeholder(new):
        return True
    # require meaningful improvement
    if len(new) >= len(old) + 20:
        return True
    if len(new) > len(old) and len(old) < 40:
        return True
    return False


def _assign(slots: dict[str, Optional[str]], key: str, cand: Optional[str]) -> None:
    clipped = _clip(cand, 1500)
    if not clipped:
        return
    if key == "root_cause" and _is_placeholder(clipped):
        return
    if _better(clipped, slots.get(key)):
        slots[key] = clipped


_BOLD_LABELS: list[tuple[str, re.Pattern[str]]] = [
    (
        "root_cause",
        re.compile(
            r"\*\*분석결과\s*\(?원인\)?\*\*\s*[:：]?\s*([\s\S]+?)(?=\n\s*[-*]*\s*\*\*|\n\s*h[1-3]\.|\n##|\Z)",
            re.I,
        ),
    ),
    (
        "resolution",
        re.compile(
            r"\*\*조치내용\*\*\s*[:：]?\s*([\s\S]+?)(?=\n\s*[-*]*\s*\*\*|\n\s*h[1-3]\.|\n##|\Z)",
            re.I,
        ),
    ),
    (
        "symptom",
        re.compile(
            r"\*\*요청이슈\*\*\s*[:：]?\s*([\s\S]+?)(?=\n\s*[-*]*\s*\*\*|\n\s*h[1-3]\.|\n##|\Z)",
            re.I,
        ),
    ),
    (
        "symptom",
        re.compile(
            r"\*\*배경\*\*\s*[:：]?\s*([\s\S]+?)(?=\n\s*[-*]*\s*\*\*|\n\s*h[1-3]\.|\n##|\Z)",
            re.I,
        ),
    ),
]


def _fill_from_text(work: str, slots: dict[str, Optional[str]]) -> list[str]:
    """Mutate slots using section + inline patterns; return section keys found."""
    # Highest precision: **bold labels** used in LLM 요약 blocks
    for key, pat in _BOLD_LABELS:
        m = pat.search(work)
        if m:
            _assign(slots, key, m.group(1))

    hits: list[tuple[str, int]] = []
    for key, pat in _SECTION_PATTERNS:
        for m in pat.finditer(work):
            hits.append((key, m.start()))
    hits.sort(key=lambda x: x[1])
    starts = [h[1] for h in hits]
    for i, (key, pos) in enumerate(hits):
        line_end = work.find("\n", pos)
        body_start = line_end + 1 if line_end >= 0 else pos
        body = _section_body(work, body_start, starts[i + 1 :])
        header_line = work[pos : line_end if line_end >= 0 else pos + 80]
        mcol = re.search(r"[:：]\s*(.+)$", header_line)
        cand = body if len(body.strip()) >= 8 else (mcol.group(1) if mcol else body)
        if cand and len(cand) > 2500:
            cand = cand[:2500]
        _assign(slots, key, cand)

    for key, pat in _INLINE_PATTERNS:
        m = pat.search(work)
        if m:
            _assign(slots, key, m.group(1))

    m = re.search(r"Workaround\s+([^\n]{10,200})", work, re.I)
    if m:
        _assign(slots, "workaround", m.group(1))

    resolved_lines = _RESOLVED_LINE.findall(work)
    if resolved_lines:
        _assign(slots, "resolution", " / ".join(resolved_lines[-3:]))

    return [k for k, _ in hits]


def extract_frame_from_markdown(
    body_md: str,
    *,
    title: str = "",
    environment: Optional[str] = None,
) -> dict[str, Any]:
    """Extract symptom/cause/resolution slots from ticket markdown."""
    text = body_md or ""
    slots: dict[str, Optional[str]] = {
        "symptom": None,
        "root_cause": None,
        "resolution": None,
        "workaround": None,
    }
    sections_found: list[str] = []

    # 1) LLM 요약 block if present
    m_sum = re.search(r"##\s*LLM\s*요약([\s\S]*?)(?:\n##\s+원본|\Z)", text, re.I)
    if m_sum:
        sections_found.extend(_fill_from_text(m_sum.group(1), slots))

    # 2) Always also scan full / original body to catch more structured tickets
    m_orig = re.search(r"##\s*원본\s*내용([\s\S]*?)(?:\n##\s+Comments|\Z)", text, re.I)
    rest = m_orig.group(1) if m_orig else text
    sections_found.extend(_fill_from_text(rest, slots))

    if not slots["symptom"] and title:
        slots["symptom"] = _clip(re.sub(r"^\[CITECTS-\d+\]\s*", "", title), 400)

    # Drop placeholder causes that add noise
    if _is_placeholder(slots.get("root_cause")):
        slots["root_cause"] = None

    components: list[str] = []
    blob = f"{title}\n{text[:12000]}"
    for pat, name in _COMPONENT_HINTS:
        if pat.search(blob) and name not in components:
            components.append(name)

    commands: list[str] = []
    for m in _CMD_RE.finditer(text[:8000]):
        cmd = m.group(1).strip()
        if cmd and cmd not in commands and len(cmd) < 160:
            commands.append(cmd)
        if len(commands) >= 8:
            break

    q = quality_score(slots["symptom"], slots["root_cause"], slots["resolution"])
    return {
        "symptom": slots["symptom"],
        "root_cause": slots["root_cause"],
        "resolution": slots["resolution"],
        "workaround": slots["workaround"],
        "components": components,
        "environment": environment,
        "commands": commands,
        "quality": q,
        "raw_extract": {
            "method": "rules_v2",
            "used_llm_summary": bool(m_sum),
            "sections_found": sorted(set(sections_found)),
        },
    }


def quality_score(
    symptom: Optional[str],
    root_cause: Optional[str],
    resolution: Optional[str],
) -> float:
    """0–1 quality: both cause and resolution present scores highest."""
    s = 0.0
    if symptom and len(symptom) >= 20:
        s += 0.25
    elif symptom:
        s += 0.1
    if root_cause and len(root_cause) >= 20:
        s += 0.35
    elif root_cause:
        s += 0.15
    if resolution and len(resolution) >= 20:
        s += 0.4
    elif resolution:
        s += 0.15
    if root_cause and resolution and len(root_cause) >= 20 and len(resolution) >= 20:
        s = min(1.0, s + 0.1)
    return round(min(1.0, s), 3)
