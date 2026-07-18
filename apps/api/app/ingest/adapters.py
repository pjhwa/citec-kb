"""Source adapters: filesystem raw → DocumentDraft."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from app.ingest.clean import clean_md

_FRONT_YAML = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_META_LINE = re.compile(r"^- \*\*(.+?)\*\*:\s*(.*)$", re.M)
_CONF_KV = re.compile(r"^([^:：\n]+)[:：]\s*(.*)$", re.M)


@dataclass
class DocumentDraft:
    source_type: str
    external_id: str
    title: str
    body_md: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_uri: Optional[str] = None
    evidence_grade: str = "B"
    environment: Optional[str] = None
    domain: Optional[str] = None
    work_type: Optional[str] = None
    path_l2: Optional[str] = None
    path_l3: Optional[str] = None
    content_hash: str = ""

    def finalize(self) -> "DocumentDraft":
        payload = f"{self.title}\n{self.body_md}\n{json.dumps(self.metadata, ensure_ascii=False, sort_keys=True)}"
        self.content_hash = hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()
        return self


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _clip(s: str | None, n: int) -> str:
    t = (s or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def iter_support_history(root: Path) -> Iterator[DocumentDraft]:
    d = root / "support_history"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        if path.name.startswith("."):
            continue
        raw = _read(path)
        title_m = re.search(r"^#\s+(.+)$", raw, re.M)
        title = title_m.group(1).strip() if title_m else path.stem
        meta: dict[str, Any] = {"filename": path.name}
        for m in _META_LINE.finditer(raw):
            meta[m.group(1).strip()] = m.group(2).strip()
        issue_key = meta.get("Issue Key") or path.stem
        body = clean_md(raw)
        grade = "A" if meta.get("Status") in ("닫힘", "Resolved", "Done") else "B"
        work = meta.get("Component")
        yield DocumentDraft(
            source_type="support_history",
            external_id=str(issue_key),
            title=_clip(title, 1000),
            body_md=body,
            metadata=meta,
            source_uri=f"file://support_history/{path.name}",
            evidence_grade=grade,
            work_type=work,
            environment="csp" if re.search(r"SCP|클라우드", title + body[:2000], re.I) else None,
        ).finalize()


def iter_tech_repo(root: Path) -> Iterator[DocumentDraft]:
    d = root / "tech_repo"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        raw = _read(path)
        meta: dict[str, Any] = {"filename": path.name}
        body = raw
        fm = _FRONT_YAML.match(raw)
        if fm:
            for line in fm.group(1).splitlines():
                if ":" in line or "：" in line:
                    # "구분 : 컨플루언스"
                    parts = re.split(r"[:：]", line, maxsplit=1)
                    if len(parts) == 2:
                        meta[parts[0].strip()] = parts[1].strip()
            body = raw[fm.end() :]
        page_id = meta.get("Page ID") or path.stem.replace("confluence_", "")
        title = meta.get("제목") or ""
        if not title or len(title) < 2:
            h = re.search(r"^#{1,3}\s+(.+)$", body, re.M)
            title = h.group(1).strip() if h else path.stem
        # Never use huge log lines as title
        if len(title) > 200:
            title = title[:200]
        directory = meta.get("디렉토리") or ""
        path_parts = [p.strip() for p in directory.split(">") if p.strip()]
        path_l2 = path_parts[2] if len(path_parts) >= 3 else (path_parts[-1] if path_parts else None)
        path_l3 = (
            f"{path_parts[2]} > {path_parts[3]}" if len(path_parts) >= 4 else path_l2
        )
        body = clean_md(body)
        yield DocumentDraft(
            source_type="tech_repo",
            external_id=str(page_id),
            title=_clip(title or path.stem, 1000),
            body_md=body,
            metadata=meta,
            source_uri=meta.get("URL") or f"file://tech_repo/{path.name}",
            evidence_grade="A",
            path_l2=path_l2,
            path_l3=path_l3,
            domain=_domain_from_path(directory),
        ).finalize()


def iter_confluence_docs(root: Path) -> Iterator[DocumentDraft]:
    # same schema as tech_repo, different source_type
    d = root / "confluence_docs"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        raw = _read(path)
        meta: dict[str, Any] = {"filename": path.name}
        body = raw
        fm = _FRONT_YAML.match(raw)
        if fm:
            for line in fm.group(1).splitlines():
                parts = re.split(r"[:：]", line, maxsplit=1)
                if len(parts) == 2:
                    meta[parts[0].strip()] = parts[1].strip()
            body = raw[fm.end() :]
        page_id = meta.get("Page ID") or path.stem
        title = meta.get("제목") or path.stem
        yield DocumentDraft(
            source_type="confluence_docs",
            external_id=str(page_id),
            title=_clip(title, 1000),
            body_md=clean_md(body),
            metadata=meta,
            source_uri=meta.get("URL"),
            evidence_grade="A",
        ).finalize()


def iter_tuning_ai(root: Path) -> Iterator[DocumentDraft]:
    d = root / "tuning_ai"
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.md")):
        raw = _read(path)
        meta: dict[str, Any] = {"filename": path.name}
        body = raw
        fm = _FRONT_YAML.match(raw)
        if fm:
            # YAML-ish key: value
            for line in fm.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
            body = raw[fm.end() :]
        issue_id = meta.get("issue_id") or path.stem
        title_m = re.search(r"^#\s+(.+)$", body, re.M)
        title = title_m.group(1).strip() if title_m else path.stem
        yield DocumentDraft(
            source_type="tuning_ai",
            external_id=str(issue_id),
            title=_clip(title, 1000),
            body_md=clean_md(body),
            metadata=meta,
            source_uri=f"file://tuning_ai/{path.name}",
            evidence_grade="A-",
            domain=meta.get("domain"),
            environment=None,
        ).finalize()


def iter_checkitems_json(root: Path) -> Iterator[DocumentDraft]:
    """Emit one DocumentDraft per checkitem for unified document store.

    Full field rows also go to checkitems table in pipeline.
    """
    path = root / "checkitems" / "checkitem_list_KO_20260609.json"
    if not path.is_file():
        # any json
        cdir = root / "checkitems"
        if not cdir.is_dir():
            return
        jsons = list(cdir.glob("*.json"))
        if not jsons:
            return
        path = jsons[0]
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("Code") or item.get("code") or "")
        if not code:
            continue
        subject = str(item.get("Subject") or "")
        area = str(item.get("Area") or "")
        body = "\n".join(
            [
                f"# [{code}] {subject}",
                f"- Area: {area}",
                f"- Category: {item.get('Category_1')}",
                f"- Subcategory: {item.get('Subcategory')}",
                "",
                "## 점검방법",
                str(item.get("점검방법") or ""),
                "",
                "## 점검기준",
                str(item.get("점검기준") or ""),
                "",
                "## 취약시 문제점",
                str(item.get("취약시 문제점") or ""),
                "",
                "## 개선방안",
                str(item.get("개선방안") or ""),
            ]
        )
        yield DocumentDraft(
            source_type="checkitem",
            external_id=code,
            title=_clip(f"[{code}] {subject}", 1000),
            body_md=body,
            metadata=item,
            source_uri=f"file://checkitems/{path.name}#{code}",
            evidence_grade="A",
            domain=area.lower() if area else None,
        ).finalize()


def _domain_from_path(directory: str) -> Optional[str]:
    d = directory or ""
    mapping = [
        ("운영체제", "os"),
        ("데이터베이스", "dbms"),
        ("스토리지", "storage"),
        ("미들웨어", "middleware"),
        ("클라우드", "cloud"),
        ("네트워크", "network"),
        ("GPU", "gpu"),
    ]
    for key, val in mapping:
        if key in d:
            return val
    return None


ADAPTERS = {
    "support_history": iter_support_history,
    "tech_repo": iter_tech_repo,
    "confluence_docs": iter_confluence_docs,
    "tuning_ai": iter_tuning_ai,
    "checkitem": iter_checkitems_json,
}


def iter_all(root: Path, sources: list[str] | None = None) -> Iterator[DocumentDraft]:
    names = sources or list(ADAPTERS.keys())
    for name in names:
        fn = ADAPTERS.get(name)
        if not fn:
            continue
        yield from fn(root)
