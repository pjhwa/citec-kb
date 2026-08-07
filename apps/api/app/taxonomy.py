"""Rule-based taxonomy enrichment for documents (filters)."""

from __future__ import annotations

import re
from typing import Any, Optional


_ENV_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bSCP\b|삼성클라우드|SKE|PaaS", re.I), "csp"),
    (re.compile(r"온프레|on-?prem|베어메탈|IDC", re.I), "onprem"),
    (re.compile(r"\bMSP\b|운영대행", re.I), "msp"),
    # Service-mesh / k8s topologies span csp and onprem alike (OpenShift, etc.),
    # so they don't pin csp/onprem/msp by themselves — "hybrid" flags the
    # topology complexity instead. This is a fallback for free-text inference
    # only; failure_bucket registration should prefer the explicit `environment`
    # parameter (see packet-capture-rca_개선지침_및_citec-kb_연동분석.md §B-1)
    # over relying on this regex to infer it after the fact.
    (re.compile(r"서비스\s*메시|service\s*mesh|Istio|Linkerd|사이드카|sidecar|mTLS", re.I), "hybrid"),
    (re.compile(r"쿠버네티스|Kubernetes|\bk8s\b|파드\s*IP|ClusterIP", re.I), "hybrid"),
]

# failure_bucket's fb_domain facet (references/failure-bucket-domains.md is the
# source of truth) mapped to the 7 fixed corpus-wide Document.domain values used
# by kb_search(area=)/kb_query. Add an entry here whenever a new fb_domain is
# registered in that file.
_FB_DOMAIN_TO_CORPUS_DOMAIN: dict[str, str] = {
    "network": "network",
    "cluster": "os",
    "windows": "os",
}

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
    if source_type == "failure_bucket" and metadata:
        fb_domain = str(metadata.get("fb_domain") or "").lower()
        if fb_domain in _FB_DOMAIN_TO_CORPUS_DOMAIN:
            return _FB_DOMAIN_TO_CORPUS_DOMAIN[fb_domain]
        # Unmapped fb_domain: leave corpus domain unset here and fall through to
        # the keyword rules below instead of dropping it entirely.
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
