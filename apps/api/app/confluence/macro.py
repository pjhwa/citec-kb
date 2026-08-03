"""Parses draw.io Diagrams for Confluence macros out of storage-format page bodies.

Uses regex instead of a strict XML parser deliberately: page bodies can contain
other macros/HTML that aren't valid standalone XML fragments, and a malformed
or unrelated fragment must not blow up diagram listing — it should just fail
soft (return no matches) so the rest of the page's diagrams still resolve.
"""

from __future__ import annotations

import re

_DRAWIO_MACRO_RE = re.compile(
    r'<ac:structured-macro[^>]*ac:name="drawio"[^>]*>(.*?)</ac:structured-macro>',
    re.DOTALL,
)
_DIAGRAM_NAME_PARAM_RE = re.compile(
    r'<ac:parameter[^>]*ac:name="diagramName"[^>]*>(.*?)</ac:parameter>',
    re.DOTALL,
)


def extract_drawio_diagram_names(body_storage_xml: str) -> list[str]:
    """Return diagramName values (in document order) of drawio macros in a
    Confluence storage-format page body. Returns [] for empty/no-match/malformed input."""
    if not body_storage_xml:
        return []
    names: list[str] = []
    for macro_match in _DRAWIO_MACRO_RE.finditer(body_storage_xml):
        param_match = _DIAGRAM_NAME_PARAM_RE.search(macro_match.group(1))
        if param_match:
            names.append(param_match.group(1).strip())
    return names


def match_attachment_for_diagram(diagram_name: str, attachments: list[dict]) -> dict | None:
    """Resolve a diagram name to its backing Confluence attachment.

    Tries an exact "<diagram_name>.drawio" filename match first, then falls
    back to a case-insensitive filename-stem comparison (Confluence can
    normalize whitespace/case in stored attachment titles).
    """
    exact = f"{diagram_name}.drawio"
    for att in attachments:
        if att.get("title") == exact:
            return att
    stem = diagram_name.strip().lower()
    for att in attachments:
        title = att.get("title") or ""
        if "." in title and title.rsplit(".", 1)[0].lower() == stem:
            return att
    return None
