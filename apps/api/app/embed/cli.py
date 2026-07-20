"""CLI: python -m app.embed.cli"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Embed active chunks into pgvector")
    p.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("EMBED_BATCH_SIZE", "16")),
        help="Chunks per DB page / encode batch (default 16 for CPU)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max chunks to embed this run (default: all pending)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    # Force line-buffered progress on log file redirects.
    for h in logging.root.handlers:
        try:
            h.flush()
        except Exception:  # noqa: BLE001
            pass

    from app.embed.job import embed_pending_chunks
    from app.embed.model import try_load_model

    ok, msg = try_load_model()
    print(json.dumps({"load": ok, "msg": msg}, ensure_ascii=False), flush=True)
    if not ok:
        return 2

    stats = embed_pending_chunks(batch_size=args.batch_size, limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
