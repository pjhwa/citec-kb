import pytest

from app.auth.principal import ANON_OPEN, ANON_RESTRICTED, Principal
from app.masking.policy import decide

P = lambda *roles: Principal(sub="u", name="u", roles=frozenset(roles), auth_via="apikey")  # noqa: E731


@pytest.mark.parametrize(
    "principal,mode,want,groups,granted,denied",
    [
        # 인증 꺼짐: 익명(전 롤 보유)도 author 수준, 원본 불가
        (ANON_OPEN, "off", False, {"pii", "net"}, False, False),
        (ANON_OPEN, "off", True, {"pii", "net"}, False, True),
        # 인증 켜짐
        (ANON_RESTRICTED, "apikey", False, {"pii", "net", "biz"}, False, False),
        (P("viewer"), "apikey", True, {"pii", "net", "biz"}, False, True),
        (P("author"), "apikey", False, {"pii", "net"}, False, False),
        (P("author"), "apikey", True, {"pii", "net"}, False, True),
        (P("senior"), "apikey", False, {"pii", "net"}, False, False),
        (P("senior"), "apikey", True, set(), True, False),
        (P("admin"), "oidc", True, set(), True, False),
        (P("viewer", "author"), "apikey", False, {"pii", "net"}, False, False),   # 높은 롤 우선
    ],
)
def test_decide(principal, mode, want, groups, granted, denied):
    d = decide(principal, mode, want)
    assert set(d.groups) == groups
    assert d.unmask_granted is granted and d.unmask_denied is denied


def test_levels():
    assert decide(P("viewer"), "apikey", False).level == "viewer"
    assert decide(P("admin"), "apikey", False).level == "senior"
    assert decide(ANON_OPEN, "off", False).level == "author"
