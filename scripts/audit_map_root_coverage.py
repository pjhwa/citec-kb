#!/usr/bin/env python3
"""Audit confluence_map root coverage (one-off, read-only).

Runs app.confluence.root_audit_cli inside the api container, so it needs the
production-side stack: live Confluence access (CONFLUENCE_BASE_URL/USERNAME/
PASSWORD) and the sources table. A dev checkout without Confluence access
cannot run it.

    python3 scripts/audit_map_root_coverage.py
    python3 scripts/audit_map_root_coverage.py --source-ids confluence_map_cldeng --depth 3

For each partial-crawl source it lists folders under the space home whose
title matches 이슈|장애|트러블슈팅|SOP|KDB|케이스 스터디 and that are not a
registered root or explicit page. A person reviews each one; add a real one
with POST /v1/confluence-map/sources/{source_id}/roots. Never add candidates
automatically (personal workspaces would get swept in).

--depth defaults to 1 (direct children of the space home). The gap that
prompted this audit sat five levels down, so a depth-1 pass alone would not
have found it — raise --depth (each level costs one request per folder).
"""

import subprocess
import sys
from pathlib import Path

project = Path(__file__).resolve().parents[1]
cmd = ["docker", "compose", "exec", "-T", "api", "python3", "-m", "app.confluence.root_audit_cli", *sys.argv[1:]]
raise SystemExit(subprocess.call(cmd, cwd=project))
