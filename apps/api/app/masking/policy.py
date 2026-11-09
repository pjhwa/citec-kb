"""Who sees what. See docs/superpowers/specs/2026-11-09-pii-masking-design.md §2."""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.principal import ROLE_ADMIN, ROLE_AUTHOR, ROLE_SENIOR, Principal

_OFF_MODES = frozenset({"", "off", "disabled", "none", "false", "0"})


@dataclass(frozen=True)
class Decision:
    level: str                 # viewer | author | senior
    groups: frozenset          # groups to mask (empty when original is granted)
    unmask_requested: bool
    unmask_granted: bool
    unmask_denied: bool


def decide(principal: Principal, auth_mode: str, unmask_requested: bool) -> Decision:
    enforced = (auth_mode or "off").lower().strip() not in _OFF_MODES
    if not enforced:
        # AUTH_MODE=off: everyone is anonymous-with-all-roles → treat as author (pii/net masked,
        # biz visible) and nobody can unmask.
        level = "author"
    elif principal.has_any(ROLE_ADMIN, ROLE_SENIOR):
        level = "senior"
    elif principal.has_any(ROLE_AUTHOR):
        level = "author"
    else:
        level = "viewer"

    groups = frozenset({"pii", "net", "biz"}) if level == "viewer" else frozenset({"pii", "net"})
    can_unmask = level == "senior"
    granted = unmask_requested and can_unmask
    if granted:
        groups = frozenset()
    return Decision(level, groups, unmask_requested, granted, unmask_requested and not can_unmask)
