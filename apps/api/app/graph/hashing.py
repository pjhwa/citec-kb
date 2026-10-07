"""Change-detection hash for app.graph.sync_state.

Deliberately NOT Document.content_hash: that hash excludes ancestor_ids on
purpose (apps/api/app/ingest/adapters.py:43-47, to avoid re-chunking on a
page move), but PARENT_OF must react to ancestor_ids changes — so this hash
includes content_hash AND whatever structured fields (ancestor_ids,
components, evidence_ref, area/category*) the caller passes in `extra`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_graph_hash(content_hash: str, extra: dict[str, Any]) -> str:
    payload = {"content_hash": content_hash, "extra": extra}
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
