"""Rule-based taxonomy enrichment for documents (filters)."""

from __future__ import annotations

import re
from typing import Any, Optional


_ENV_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bSCP\b|삼성클라우드|SKE|PaaS", re.I), "csp"),
    (re.compile(r"온프레|on-?prem|베어메탈|IDC", re.I), "onprem"),
    (re.compile(r"\bMSP\b|운영대행", re.I), "msp"),
]

_DOMAIN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Linux|리눅스|kernel|sysctl|OS hang", re.I), "os"),
    (re.compile(r"Oracle|HANA|MySQL|Tibero|Postgre|DB2|Greenplum|GPDB|SQL", re.I), "dbms"),
    (re.compile(r"스토리지|NetApp|Ceph|NAS|SAN|disk latency|multipath", re.I), "storage"),
    (re.compile(r"네트워크|NIC|MTU|VXLAN|GRO|LB|방화벽|NSX", re.I), "network"),
    (re.compile(r"VMware|ESXi|vCenter|가상화", re.I), "virtualization"),
    (re.compile(r"Kubernetes|k8s|K8S|SKE|Redis|WAS|Tomcat|WebLogic", re.I), "middleware"),
    (re.compile(r"클라우드|SCP", re.I), "cloud"),
]


def infer_environment(title: str, body: str, metadata: dict[str, Any] | None = None) -> Optional[str]:
    blob = f"{title}\n{body[:3000]}"
    for pat, env in _ENV_RULES:
        if pat.search(blob):
            return env
    return None


def infer_domain(
    title: str,
    body: str,
    *,
    path_l2: Optional[str] = None,
    path_l3: Optional[str] = None,
    source_type: Optional[str] = None,
    metadata: dict[str, Any] | None = None,
) -> Optional[str]:
    path = f"{path_l2 or ''} {path_l3 or ''}"
    path_map = {
        "운영체제": "os",
        "데이터베이스": "dbms",
        "스토리지": "storage",
        "미들웨어": "middleware",
        "클라우드": "cloud",
        "네트워크": "network",
        "GPU": "gpu",
    }
    for k, v in path_map.items():
        if k in path:
            return v
    if source_type == "checkitem" and metadata:
        area = str(metadata.get("Area") or "")
        if area:
            return area.lower().replace(" ", "_")
    blob = f"{title}\n{body[:3000]}"
    for pat, dom in _DOMAIN_RULES:
        if pat.search(blob):
            return dom
    return None


def enrich_draft_fields(
    *,
    title: str,
    body: str,
    source_type: str,
    metadata: dict[str, Any],
    path_l2: Optional[str],
    path_l3: Optional[str],
    environment: Optional[str],
    domain: Optional[str],
    work_type: Optional[str],
) -> dict[str, Optional[str]]:
    env = environment or infer_environment(title, body, metadata)
    dom = domain or infer_domain(
        title, body, path_l2=path_l2, path_l3=path_l3, source_type=source_type, metadata=metadata
    )
    wt = work_type
    if not wt and source_type == "support_history":
        wt = metadata.get("Component")
    return {"environment": env, "domain": dom, "work_type": wt}
