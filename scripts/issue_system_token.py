#!/usr/bin/env python3
"""시스템 등록자용 API 토큰을 발급/회수한다 (config/auth.json 편집).

  python3 scripts/issue_system_token.py --name insight-ops@v1.4
  python3 scripts/issue_system_token.py --name insight-ops@v1.4 --rotate   # 교체(기존 토큰 폐기)
  python3 scripts/issue_system_token.py --revoke insight-ops@v1.4

발급한 토큰은 stdout에 한 번만 출력한다(파일 외 어디에도 남기지 않는다). 서버는 auth.json의
mtime/크기가 바뀌면 다음 요청부터 새 표를 읽으므로 재시작이 필요 없다.
시스템 토큰은 author 롤만 허용한다. senior/admin은 사람용이라 --allow-privileged가 필요하다.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
import tempfile
from pathlib import Path

SYSTEM_ROLES = ("author",)
ALL_ROLES = ("viewer", "author", "senior", "admin")


def _load(path: Path) -> dict:
    if not path.exists():
        return {"tokens": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("tokens"), dict):
        data["tokens"] = {}
    return data


def _write_atomic(path: Path, data: dict) -> None:
    """Write via temp file + rename so the server never reads a half-written file
    (a corrupt auth.json makes every token invalid)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".auth.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _warn_perms(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        print(f"경고: {path} 권한이 {oct(mode)}입니다. 소유자 외 접근을 막으세요: chmod 600 {path}", file=sys.stderr)


def issue(path: Path, name: str, roles: list[str], rotate: bool) -> str:
    data = _load(path)
    existing = [t for t, m in data["tokens"].items() if isinstance(m, dict) and m.get("name") == name]
    if existing and not rotate:
        raise SystemExit(f"오류: '{name}' 토큰이 이미 있습니다. 교체하려면 --rotate (기존 토큰은 폐기됨).")
    for t in existing:
        del data["tokens"][t]
    token = secrets.token_urlsafe(32)
    data["tokens"][token] = {"sub": name, "name": name, "roles": roles}
    _write_atomic(path, data)
    return token


def revoke(path: Path, name: str) -> int:
    data = _load(path)
    victims = [t for t, m in data["tokens"].items() if isinstance(m, dict) and m.get("name") == name]
    for t in victims:
        del data["tokens"][t]
    if victims:
        _write_atomic(path, data)
    return len(victims)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", help="토큰 이름. created_by와 같은 형식(예: insight-ops@v1.4)")
    g.add_argument("--revoke", metavar="NAME", help="해당 이름의 토큰을 제거")
    ap.add_argument("--roles", default="author", help="콤마 구분 롤(기본 author)")
    ap.add_argument("--out", default="config/auth.json", help="토큰 파일 경로(기본 config/auth.json)")
    ap.add_argument("--rotate", action="store_true", help="같은 이름의 기존 토큰을 폐기하고 새로 발급")
    ap.add_argument("--allow-privileged", action="store_true", help="senior/admin/viewer 등 author 외 롤 허용(사람용)")
    args = ap.parse_args(argv)
    path = Path(args.out)

    if args.revoke:
        n = revoke(path, args.revoke)
        if n == 0:
            print(f"'{args.revoke}' 토큰이 없습니다.", file=sys.stderr)
            return 1
        print(f"회수됨: {args.revoke} ({n}건). 다음 요청부터 401.", file=sys.stderr)
        if path.exists():
            _warn_perms(path)
        return 0

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    bad = [r for r in roles if r not in ALL_ROLES]
    if bad or not roles:
        raise SystemExit(f"오류: 알 수 없는 롤 {bad or roles}. 허용: {list(ALL_ROLES)}")
    if not args.allow_privileged and any(r not in SYSTEM_ROLES for r in roles):
        raise SystemExit("오류: 시스템 토큰은 author 롤만 허용합니다(사람용 롤은 --allow-privileged).")
    token = issue(path, args.name, roles, args.rotate)
    _warn_perms(path)
    print(token)
    print(f"발급됨: {args.name} roles={roles} → {path} (토큰은 위 한 줄뿐입니다. 안전하게 전달하세요)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
