"""CLI: python -m app.confluence.map_space_setup [--apply] [--skip-verify] [--print-source-ids]

Switches the partial-crawl confluence_map spaces to whole-space crawls
rooted at each space's top page, adds new spaces, and removes retired ones.
Run it through scripts/map_space_setup.sh (needs the api container: DB plus
live Confluence access).

Default is a dry run that prints the plan. `--apply` writes it. Neither
crawls anything: indexing the new roots is a separate, long step
(scripts/map_backfill.sh --from-scratch --source-ids ...), which the shell
wrapper can chain with --backfill.

What --apply does, per SPACE_HOMES entry:
  * existing source: config.roots is REPLACED by {top page: label}. The old
    narrower roots sit inside that tree, so keeping them would fetch every
    page twice. explicit_pages (personal-workspace seeds outside the tree)
    and space_key/space_name/status/last_sync_at are kept; a stale
    checkpoint keyed by the old root ids is dropped.
  * new source: created active, with its cursor seeded to now. Without a
    cursor the daily sync_map would start its own full bootstrap crawl of
    the space next to the backfill.
  * REMOVE_SOURCES: the source row is deleted and any active documents of
    that space are archived (search skips archived), not deleted.

Every entry is checked against Confluence first (the page exists, belongs to
the expected space, has no ancestors); one failure aborts the whole run so a
typo'd page id never becomes a crawl root. Nothing is applied while a
sync/backfill holds the run lock.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# (space_key, top page id). Existing spaces are matched to their source by
# space_key (case-insensitive); LOOKIN/TechRepo/ServiceExcellenceTeam/
# ICLOUDUT already crawl their whole space and are deliberately not listed,
# and neither is SPC (single seed pages, no space-wide crawl requested).
SPACE_HOMES: list[tuple[str, str]] = [
    ("DevOps001", "338021717"),
    ("Openstack101", "361297136"),
    ("sysops", "1423765569"),
    ("CLDENG", "269157800"),
    ("EMCloud", "74026328"),
    ("DFTRTS", "74926031"),
    ("GUID", "7307288"),
    ("STORAGE", "644458153"),
    ("SCPTechTree", "636895667"),
    ("CATT", "60932540"),
    ("SI", "73609130"),
]
REMOVE_SOURCES: list[str] = ["confluence_map_genaibusiness"]


class PlanError(Exception):
    pass


def source_id_for(space_key: str) -> str:
    from app.routers.confluence_map import _slugify_space_key

    return f"confluence_map_{_slugify_space_key(space_key)}"


def home_label(space_name: str) -> str:
    return f"전체 공간 ({space_name} Home)"


def build_plan(
    current: dict[str, dict[str, Any]],
    info: dict[str, dict[str, Any]],
    homes: list[tuple[str, str]] = SPACE_HOMES,
    remove: list[str] = REMOVE_SOURCES,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pure planning step. Returns (actions, errors); apply only if no errors.

    current: {source_id: {"config": {...}, "status": str}} for confluence_map
    sources. info: {home_page_id: {"space_key", "space_name", "title",
    "ancestors": int}} as read from Confluence.
    """
    actions: list[dict[str, Any]] = []
    errors: list[str] = []
    for space_key, home_id in homes:
        sid = source_id_for(space_key)
        got = info.get(home_id)
        if got is None:
            errors.append(f"{space_key}: page {home_id} could not be read from Confluence")
            continue
        if str(got["space_key"]).lower() != space_key.lower():
            errors.append(
                f"{space_key}: page {home_id} belongs to space {got['space_key']!r}, not {space_key!r}"
            )
            continue
        warnings = []
        if got.get("ancestors"):
            warnings.append(
                f"page {home_id} ({got['title']!r}) has {got['ancestors']} ancestor(s): "
                "it is not the space's top page, so part of the space would stay uncrawled"
            )
        existing = current.get(sid)
        if existing is None:
            actions.append(
                {
                    "op": "create",
                    "source_id": sid,
                    "space_key": got["space_key"],
                    "space_name": got["space_name"],
                    "roots": {home_id: home_label(got["space_name"])},
                    "warnings": warnings,
                }
            )
            continue
        cfg = existing["config"]
        if str(cfg.get("space_key", "")).lower() != space_key.lower():
            errors.append(
                f"{space_key}: source {sid} has space_key {cfg.get('space_key')!r}; refusing to rewrite it"
            )
            continue
        new_roots = {home_id: home_label(cfg.get("space_name") or got["space_name"])}
        old_roots = dict(cfg.get("roots") or {})
        actions.append(
            {
                "op": "unchanged" if old_roots == new_roots else "set_roots",
                "source_id": sid,
                "space_key": cfg["space_key"],
                "old_roots": old_roots,
                "roots": new_roots,
                "explicit_pages": dict(cfg.get("explicit_pages") or {}),
                "warnings": warnings,
            }
        )
    for sid in remove:
        if sid in current:
            actions.append({"op": "remove", "source_id": sid, "space_key": current[sid]["config"].get("space_key")})
    return actions, errors


def target_source_ids(actions: list[dict[str, Any]]) -> list[str]:
    """Sources that need the follow-up backfill (everything not removed or unchanged)."""
    return [a["source_id"] for a in actions if a["op"] in {"create", "set_roots"}]


def load_current() -> dict[str, dict[str, Any]]:
    from app.db.models import Source
    from app.db.session import session_scope

    with session_scope() as session:
        rows = session.query(Source).filter(Source.type == "confluence_map").all()
        return {r.id: {"config": dict(r.config or {}), "status": r.status} for r in rows}


async def _fetch_info(homes: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    from app.confluence.client import ConfluenceClient
    from app.settings import get_settings

    client = ConfluenceClient(get_settings())
    out: dict[str, dict[str, Any]] = {}
    async with client._http() as http:
        for _key, page_id in homes:
            try:
                resp = await http.get(
                    f"/rest/api/content/{page_id}", params={"expand": "space,ancestors"}
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 — reported per page by build_plan
                logger.warning("confluence lookup failed page=%s: %s", page_id, exc)
                continue
            space = data.get("space") or {}
            out[page_id] = {
                "space_key": space.get("key") or "",
                "space_name": space.get("name") or space.get("key") or "",
                "title": data.get("title") or "",
                "ancestors": len(data.get("ancestors") or []),
            }
    return out


def _skip_verify_info(homes: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    return {
        pid: {"space_key": key, "space_name": key, "title": "(unverified)", "ancestors": 0}
        for key, pid in homes
    }


def apply_plan(actions: list[dict[str, Any]]) -> None:
    from app.confluence.sync import _LAST_SYNC_MARGIN
    from app.db.models import Document, Source
    from app.db.session import session_scope

    now = datetime.now(timezone.utc) - _LAST_SYNC_MARGIN
    with session_scope() as session:
        for a in actions:
            if a["op"] == "create":
                session.add(
                    Source(
                        id=a["source_id"],
                        type="confluence_map",
                        name=f"Confluence Map {a['space_key']}",
                        config={
                            "space_key": a["space_key"],
                            "space_name": a["space_name"],
                            "roots": a["roots"],
                            "explicit_pages": {},
                        },
                        status="active",
                        last_sync_at=now,
                    )
                )
            elif a["op"] == "set_roots":
                src = session.get(Source, a["source_id"])
                config = dict(src.config or {})
                config["roots"] = a["roots"]
                config.pop("checkpoint", None)
                src.config = config
            elif a["op"] == "remove":
                src = session.get(Source, a["source_id"])
                space_key = (src.config or {}).get("space_key")
                if space_key:
                    docs = (
                        session.query(Document)
                        .filter(
                            Document.source_type == "confluence_map",
                            Document.status == "active",
                            Document.metadata_["space_key"].astext == space_key,
                        )
                        .all()
                    )
                    for d in docs:
                        d.status = "archived"
                    a["archived_documents"] = len(docs)
                session.delete(src)


def format_plan(actions: list[dict[str, Any]], errors: list[str]) -> str:
    lines: list[str] = []
    for a in actions:
        op = a["op"]
        if op == "create":
            lines.append(f"[create]    {a['source_id']} ({a['space_key']}) roots={a['roots']}")
        elif op == "set_roots":
            lines.append(
                f"[set_roots] {a['source_id']} ({a['space_key']})\n"
                f"              old={a['old_roots']}\n              new={a['roots']}"
                + (f"\n              kept explicit_pages={list(a['explicit_pages'])}" if a["explicit_pages"] else "")
            )
        elif op == "unchanged":
            lines.append(f"[unchanged] {a['source_id']} ({a['space_key']})")
        elif op == "remove":
            lines.append(f"[remove]    {a['source_id']} ({a.get('space_key')}) — source deleted, its documents archived")
        for w in a.get("warnings") or []:
            lines.append(f"  WARNING {a['source_id']}: {w}")
    for e in errors:
        lines.append(f"ERROR {e}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the plan (default: dry run)")
    ap.add_argument(
        "--skip-verify",
        action="store_true",
        help="do not call Confluence; new spaces are named after their key (not recommended)",
    )
    ap.add_argument(
        "--print-source-ids",
        action="store_true",
        help="print only the comma list of sources that need the backfill, then exit",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    current = load_current()
    info = _skip_verify_info(SPACE_HOMES) if args.skip_verify else asyncio.run(_fetch_info(SPACE_HOMES))
    actions, errors = build_plan(current, info)
    if args.print_source_ids:
        print(",".join(target_source_ids(actions)))
        return 1 if errors else 0
    print(format_plan(actions, errors))
    if errors:
        print("\nAborted: fix the errors above; nothing was changed.", file=sys.stderr)
        return 1
    if not args.apply:
        print("\nDry run — nothing changed. Re-run with --apply to write this plan.")
        return 0

    from app.confluence.map_sync import is_sync_running

    if is_sync_running():
        print("Aborted: a sync/backfill is running (run lock held). Try again after it ends.", file=sys.stderr)
        return 2
    apply_plan(actions)
    print("\nApplied.")
    ids = target_source_ids(actions)
    if ids:
        print("Next (long-running): scripts/map_backfill.sh --from-scratch --source-ids " + ",".join(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
