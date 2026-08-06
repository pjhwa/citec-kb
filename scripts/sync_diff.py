#!/usr/bin/env python3
"""운영→개발 incremental DB sync: 매니페스트 diff 계산 (순수 로직, DB/네트워크 의존 없음).

매니페스트 포맷: gzip TSV, 한 줄에 `<table>\t<id>\t<md5hash>`.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

TABLES = (
    "documents",
    "document_sections",
    "chunks",
    "checkitems",
    "issue_frames",
    "failure_buckets",
    "raw_files",  # data/raw 첨부파일: id=상대경로, hash=sha256
)


def read_manifest(path: Path) -> dict[str, dict[str, str]]:
    """gzip TSV 매니페스트를 {table: {id: hash}} 로 파싱한다."""
    manifest: dict[str, dict[str, str]] = {t: {} for t in TABLES}
    with gzip.open(path, "rt", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if not row:
                continue
            tbl, doc_id, h = row
            manifest.setdefault(tbl, {})[doc_id] = h
    return manifest


def compute_diff(
    dev_manifest: dict[str, dict[str, str]],
    ops_manifest: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    """테이블별로 운영에만 있거나(신규) hash가 다른(변경) id 목록. 삭제는 무시한다."""
    diff: dict[str, list[str]] = {}
    for tbl in TABLES:
        ops_rows = ops_manifest.get(tbl, {})
        dev_rows = dev_manifest.get(tbl, {})
        changed = [doc_id for doc_id, h in ops_rows.items() if dev_rows.get(doc_id) != h]
        diff[tbl] = sorted(changed)
    return diff


def write_diff_files(diff: dict[str, list[str]], out_dir: Path) -> dict[str, int]:
    """테이블별 <table>.ids 파일(줄바꿈 구분 id)을 변경분이 있는 테이블에만 쓴다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for tbl, ids in diff.items():
        counts[tbl] = len(ids)
        if ids:
            (out_dir / f"{tbl}.ids").write_text("\n".join(ids) + "\n")
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="개발/운영 DB 매니페스트 diff 계산")
    p.add_argument("--dev-manifest", required=True, type=Path)
    p.add_argument("--ops-manifest", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    args = p.parse_args(argv)

    dev_manifest = read_manifest(args.dev_manifest)
    ops_manifest = read_manifest(args.ops_manifest)
    diff = compute_diff(dev_manifest, ops_manifest)
    counts = write_diff_files(diff, args.out_dir)

    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
