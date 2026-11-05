"""Admin-page token issuing: hashed store at <data_dir>/auth_tokens.json.

Only the sha256 digest of a token is stored, so neither the file nor the list endpoint can
reveal a token; the raw value is returned once, at issue time. config/auth.json (read-only in
the api container) and AUTH_TOKENS_JSON stay host-managed (scripts/issue_system_token.py).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from app.auth.principal import (
    ALL_ROLES,
    HASH_PREFIX,
    _build_token_table,
    admin_tokens_path,
    token_digest,
)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
PRIVILEGED = frozenset({"senior", "admin"})


class TokenError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _read() -> dict[str, Any]:
    path = admin_tokens_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"tokens": {}}
    if not isinstance(data.get("tokens"), dict):
        raise TokenError(500, f"{path.name} 형식이 올바르지 않습니다. 파일을 점검하세요.")
    return data


def _write(data: dict[str, Any]) -> None:
    path = admin_tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _locked():
    lock = admin_tokens_path().with_name(admin_tokens_path().name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock, "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def list_tokens() -> list[dict[str, Any]]:
    """Every credential the server accepts, without secrets."""
    meta = _read()["tokens"]
    out = []
    for key, p in _build_token_table().items():
        hashed = key.startswith(HASH_PREFIX)
        m = meta.get(key, {}) if hashed else {}
        out.append({
            "name": p.name,
            "roles": sorted(p.roles),
            "source": "admin" if hashed else "config",
            "fingerprint": key[len(HASH_PREFIX):len(HASH_PREFIX) + 12] if hashed else None,
            "created_at": m.get("created_at"),
            "created_by": m.get("created_by"),
            "note": m.get("note"),
        })
    return sorted(out, key=lambda r: (r["source"], r["name"]))


def issue(name: str, roles: list[str], allow_privileged: bool, note: str | None,
          rotate: bool, issued_by: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise TokenError(422, "이름은 영문/숫자로 시작하고 [A-Za-z0-9._@-]만 쓸 수 있습니다(최대 128자). 예: insight-ops@v1.4")
    bad = [r for r in roles if r not in ALL_ROLES]
    if bad or not roles:
        raise TokenError(422, f"roles는 {list(ALL_ROLES)} 중에서 하나 이상이어야 합니다.")
    if PRIVILEGED.intersection(roles):
        if not allow_privileged:
            raise TokenError(422, "senior/admin 토큰은 사람용입니다. allow_privileged=true를 명시해야 발급됩니다.")
        if "@" in name:
            raise TokenError(422, "시스템 형식 이름(<시스템>@<판>)에는 senior/admin을 줄 수 없습니다.")
    fh = _locked()
    try:
        data = _read()
        table = _build_token_table()
        mine = {k for k, v in data["tokens"].items() if v.get("name") == name}
        elsewhere = [k for k, p in table.items() if p.name == name and k not in mine]
        if elsewhere:
            raise TokenError(409, f"'{name}'은(는) config/auth.json 또는 환경변수에 이미 있습니다. 호스트에서 scripts/issue_system_token.py로 관리하세요.")
        if mine and not rotate:
            raise TokenError(409, f"'{name}' 토큰이 이미 있습니다. 교체하려면 rotate=true(기존 토큰은 즉시 폐기됩니다).")
        for k in mine:
            del data["tokens"][k]
        token = secrets.token_urlsafe(32)
        key = token_digest(token)
        data["tokens"][key] = {
            "sub": name, "name": name, "roles": sorted(roles), "note": (note or "").strip() or None,
            "created_by": issued_by, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _write(data)
    finally:
        fh.close()
    return {"token": token, "name": name, "roles": sorted(roles), "fingerprint": key[len(HASH_PREFIX):len(HASH_PREFIX) + 12],
            "rotated": bool(mine)}


def revoke(name: str) -> int:
    fh = _locked()
    try:
        data = _read()
        keys = [k for k, v in data["tokens"].items() if v.get("name") == name]
        if not keys:
            if any(p.name == name for p in _build_token_table().values()):
                raise TokenError(409, f"'{name}'은(는) config/auth.json 또는 환경변수 토큰입니다. 호스트에서 issue_system_token.py --revoke로 회수하세요.")
            raise TokenError(404, f"'{name}' 토큰이 없습니다.")
        for k in keys:
            del data["tokens"][k]
        _write(data)
    finally:
        fh.close()
    return len(keys)
