# 운영→개발 Incremental DB Sync 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영 DB의 신규/변경 행만 (`documents`, `document_sections`, `chunks`, `checkitems`,
`issue_frames`, `failure_buckets`) + `data/raw` 첨부파일 중 신규/변경분만 개발서버로 옮기는
스크립트 3종 + diff 로직을 만든다. 임베딩은 옮기지 않고 개발에서 재생성한다.

**Architecture:** 콘텐츠 해시 매니페스트 diff. 순수 diff 계산(`sync_diff.py`, stdlib만 사용, 단위
테스트 가능)과 I/O(bash + `docker compose exec postgres psql` / 파일시스템, 기존 `out.sh`/`in.sh`
관례를 따름)를 분리한다. DB 6테이블과 `data/raw` 파일은 동일한 "id → hash" 매니페스트 개념을
공유한다 — 파일은 `raw_files`라는 7번째 "테이블"처럼 취급되고(id=상대경로, hash=sha256),
`sync_diff.py`는 이 구분을 모른 채 동일 로직으로 diff한다. 세 스크립트가 파이프라인을 이룬다:
`sync_manifest.sh`(양쪽에서 실행, DB 6테이블 + raw 파일 해시) →
`sync_export.sh`(운영에서 실행, diff 계산 + DB export + 변경 파일 tar) →
`sync_apply.sh`(개발에서 실행, DB 단일 트랜잭션 적용 + 파일 반영 + 재임베딩 트리거).

**Tech Stack:** Bash (`set -euo pipefail`), `psql` via `docker compose exec -T postgres`, Python 3
표준 라이브러리만 사용하는 diff 모듈, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-06-incremental-data-sync-design.md`

---

## 사전 지식 (모든 태스크에 필요)

- 스택은 `docker compose`로 기동되며 서비스명은 `postgres`, `api`. Postgres 접속 정보는 환경변수
  `POSTGRES_USER`(기본 `citec`), `POSTGRES_DB`(기본 `citec_knowledge`)로 결정한다
  (`scripts/out.sh`의 `build_data_bundle`와 동일 관례).
- 6개 테이블의 컬럼 목록(순서 포함, `apps/api/app/db/models.py` 기준):
  - `documents`: `id, source_id, source_type, external_id, title, body_md, metadata, content_hash, version, status, source_uri, lang, evidence_grade, environment, domain, work_type, path_l2, path_l3, ingested_at, created_at, updated_at`
  - `document_sections`: `id, document_id, heading_path, level, body_md, token_count, ordinal`
  - `chunks`: `id, document_id, section_id, ordinal, text, header_context, token_count, tsv, is_active, created_at`
  - `checkitems`: `id, code, lang, area, category, category_1, subcategory, subject, check_method, check_criteria, check_result, risk_if_vulnerable, remediation, raw, tsv, document_id, created_at`
  - `issue_frames`: `id, document_id, symptom, root_cause, resolution, workaround, components, environment, commands, quality, raw_extract, created_at, updated_at`
  - `failure_buckets`: `id, document_id, bucket_name, protocol, symptom, discriminating_signals, counter_signals, root_cause, recommended_action, confidence, support_count, counter_count, evidence_grade, created_by, created_at, updated_at`
- 재임베딩 트리거: `docker compose exec -T api python -m app.embed.cli` — 모델 기준으로
  embedding이 없는 active chunk만 골라 배치 임베딩한다 (`apps/api/app/embed/job.py`). 새로 들어온
  chunk에는 embedding이 없으므로 자동으로 포함되고, 기존 chunk는 건드리지 않는다.
- 알려진 제약: `\copy ... FROM STDIN`은 데이터 스트림 안에 정확히 `\.`로만 이루어진 줄이 있으면
  스트림 종료로 오인한다. `body_md`/`text` 같은 자유 텍스트 컬럼에 그런 줄이 우연히 있을 극단적인
  경우는 이번 범위에서 다루지 않는다 (기존 `out.sh`의 `pg_dump` 파이프도 동일한 종류의 신뢰를 전제로
  한다).
- 첨부파일은 `${PROJECT_DIR}/data/raw` 밑에 있다 (`checkitems/`, `vendor_docs/`,
  `incident_reports/`, `support_history/`, `tuning_ai/`, `tech_repo/`, `confluence_docs/` 서브
  디렉터리). 매니페스트에서는 `raw_files`라는 이름으로 `data/raw` 기준 상대경로를 id로,
  `sha256sum`을 hash로 쓴다. 기존 `data/raw_manifest.json`(소스별 파일 개수 집계)은 파일 단위
  해시가 없어 이번 diff에 재사용하지 않는다 — 별개로 새로 계산한다.

---

### Task 1: `sync_diff.py` — 순수 diff 로직 (TDD)

**Files:**
- Create: `scripts/sync_diff.py`
- Test: `scripts/tests/test_sync_diff.py`

- [ ] **Step 1: 테스트 디렉터리 생성 및 실패하는 테스트 작성**

`scripts/tests/test_sync_diff.py`:

```python
import gzip
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync_diff import compute_diff, read_manifest, write_diff_files


def _write_manifest(path: Path, rows: list[tuple[str, str, str]]) -> None:
    with gzip.open(path, "wt", newline="") as f:
        for tbl, doc_id, h in rows:
            f.write(f"{tbl}\t{doc_id}\t{h}\n")


def test_compute_diff_new_id_included():
    dev = {"documents": {}}
    ops = {"documents": {"doc-1": "hash-a"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == ["doc-1"]


def test_compute_diff_changed_hash_included():
    dev = {"documents": {"doc-1": "hash-old"}}
    ops = {"documents": {"doc-1": "hash-new"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == ["doc-1"]


def test_compute_diff_unchanged_excluded():
    dev = {"documents": {"doc-1": "hash-a"}}
    ops = {"documents": {"doc-1": "hash-a"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == []


def test_compute_diff_empty_dev_manifest_is_full_export():
    dev = {"documents": {}}
    ops = {"documents": {"doc-1": "h1", "doc-2": "h2"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == ["doc-1", "doc-2"]


def test_compute_diff_ignores_ids_missing_from_ops():
    # dev has doc-1 that ops no longer has (deleted on ops) — must be ignored, not reported.
    dev = {"documents": {"doc-1": "hash-a"}}
    ops = {"documents": {}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == []


def test_compute_diff_covers_raw_files_namespace():
    # raw_files is just another "table" to this module — same diff semantics.
    dev = {"raw_files": {"checkitems/a.json": "hash-a"}}
    ops = {"raw_files": {"checkitems/a.json": "hash-a", "checkitems/b.json": "hash-b"}}
    diff = compute_diff(dev, ops)
    assert diff["raw_files"] == ["checkitems/b.json"]


def test_read_manifest_roundtrip(tmp_path):
    path = tmp_path / "manifest.tsv.gz"
    _write_manifest(
        path,
        [
            ("documents", "doc-1", "hash-a"),
            ("documents", "doc-2", "hash-b"),
            ("chunks", "chunk-1", "hash-c"),
        ],
    )
    manifest = read_manifest(path)
    assert manifest["documents"] == {"doc-1": "hash-a", "doc-2": "hash-b"}
    assert manifest["chunks"] == {"chunk-1": "hash-c"}
    assert manifest["checkitems"] == {}


def test_write_diff_files_only_for_changed_tables(tmp_path):
    diff = {"documents": ["doc-1"], "chunks": []}
    counts = write_diff_files(diff, tmp_path)
    assert counts == {"documents": 1, "chunks": 0}
    assert (tmp_path / "documents.ids").read_text() == "doc-1\n"
    assert not (tmp_path / "chunks.ids").exists()


def test_cli_writes_counts_json(tmp_path):
    dev_path = tmp_path / "dev.tsv.gz"
    ops_path = tmp_path / "ops.tsv.gz"
    _write_manifest(dev_path, [("documents", "doc-1", "hash-a")])
    _write_manifest(ops_path, [("documents", "doc-1", "hash-a"), ("documents", "doc-2", "hash-b")])
    out_dir = tmp_path / "diff"

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "sync_diff.py"),
            "--dev-manifest", str(dev_path),
            "--ops-manifest", str(ops_path),
            "--out-dir", str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    counts = json.loads(result.stdout)
    assert counts["documents"] == 1
    assert (out_dir / "documents.ids").read_text().strip() == "doc-2"
```

- [ ] **Step 2: 테스트 실행해서 실패하는지 확인**

Run: `cd /home/citec/dev/citec-kb && python3 -m pytest scripts/tests/test_sync_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sync_diff'`

- [ ] **Step 3: `sync_diff.py` 구현**

`scripts/sync_diff.py`:

```python
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
```

- [ ] **Step 4: 테스트 실행해서 통과하는지 확인**

Run: `cd /home/citec/dev/citec-kb && python3 -m pytest scripts/tests/test_sync_diff.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add scripts/sync_diff.py scripts/tests/test_sync_diff.py
git commit -m "feat(sync): add pure diff logic for incremental DB manifest comparison"
```

---

### Task 2: `sync_manifest.sh` — 매니페스트 생성 (개발/운영 공통)

**Files:**
- Create: `scripts/sync_manifest.sh`

- [ ] **Step 1: 스크립트 작성**

`scripts/sync_manifest.sh`:

```bash
#!/usr/bin/env bash
# sync_manifest.sh — 6개 테이블의 (table, id, md5hash) 매니페스트를 gzip TSV로 생성.
# 개발/운영 양쪽에서 동일하게 실행 (incremental sync의 diff 계산 전 단계).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_FILE=""
TS="$(date '+%Y-%m-%d_%H%M%S')"

usage() {
  cat <<'EOF'
sync_manifest.sh — documents/document_sections/chunks/checkitems/issue_frames/failure_buckets
6개 테이블 + data/raw 첨부파일(raw_files)의 (table, id, hash) 매니페스트를 gzip TSV로 생성한다.

USAGE
  scripts/sync_manifest.sh [--out FILE] [--project DIR]

옵션:
  --out FILE       출력 경로 (기본: ~/tmp/citec-kb-manifest-<TS>.tsv.gz)
  --project DIR    레포 루트 (기본: 이 스크립트 상위)
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --out) OUT_FILE="${2:-}"; shift 2 ;;
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 1 ;;
  esac
done

OUT_FILE="${OUT_FILE:-${HOME}/tmp/citec-kb-manifest-${TS}.tsv.gz}"
mkdir -p "$(dirname "$OUT_FILE")"

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

TABLES=(documents document_sections chunks checkitems issue_frames failure_buckets)

echo "[sync_manifest] project=${PROJECT_DIR} out=${OUT_FILE}" >&2

TMP_RAW="$(mktemp)"
trap 'rm -f "$TMP_RAW"' EXIT

for tbl in "${TABLES[@]}"; do
  echo "[sync_manifest] ${tbl}" >&2
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
    psql -q -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -At -F $'\t' \
    -c "SELECT '${tbl}', id, md5(t::text) FROM ${tbl} t" >> "$TMP_RAW"
done

RAW_DIR="${PROJECT_DIR}/data/raw"
if [[ -d "$RAW_DIR" ]]; then
  echo "[sync_manifest] raw_files" >&2
  # 파일명에 공백이 있어도 안전하도록 NUL 구분 + 파일당 sha256sum 개별 호출
  # (sha256sum 배치 출력 "<hash>  <path>"를 공백 기준으로 재파싱하면 공백 포함 경로에서 깨짐)
  while IFS= read -r -d '' relpath; do
    hash="$(sha256sum "${RAW_DIR}/${relpath}" | cut -d' ' -f1)"
    printf 'raw_files\t%s\t%s\n' "$relpath" "$hash"
  done < <(cd "$RAW_DIR" && find . -type f -printf '%P\0') >> "$TMP_RAW"
else
  echo "[sync_manifest] data/raw 없음 — raw_files 건너뜀" >&2
fi

gzip -c "$TMP_RAW" > "$OUT_FILE"
n=$(wc -l < "$TMP_RAW")
echo "[sync_manifest] 완료: ${OUT_FILE} (${n} rows)" >&2
```

- [ ] **Step 2: 실행 권한 부여**

Run: `chmod +x /home/citec/dev/citec-kb/scripts/sync_manifest.sh`

- [ ] **Step 3: 현재 실행 중인 개발 스택 대상으로 동작 확인**

Run:
```bash
cd /home/citec/dev/citec-kb
docker compose ps postgres   # Up 상태 확인
scripts/sync_manifest.sh --out /tmp/claude-1000/citec-kb-manifest-test.tsv.gz
zcat /tmp/claude-1000/citec-kb-manifest-test.tsv.gz | cut -f1 | sort -u
```
Expected: 마지막 출력이 정확히 7줄 — `checkitems`, `chunks`, `document_sections`, `documents`,
`failure_buckets`, `issue_frames`, `raw_files` (알파벳 정렬). `data/raw`에 파일이 5000개 이상
있으므로(기존 `raw_manifest.json` 기준 `total_files: 5007`) 이 단계는 몇 초~수십 초 걸릴 수 있다.

- [ ] **Step 4: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add scripts/sync_manifest.sh
git commit -m "feat(sync): add sync_manifest.sh to generate table hash manifests"
```

---

### Task 3: `sync_export.sh` — 운영: diff 계산 + export

**Files:**
- Create: `scripts/sync_export.sh`

- [ ] **Step 1: 스크립트 작성**

`scripts/sync_export.sh`:

```bash
#!/usr/bin/env bash
# sync_export.sh — 운영에서 실행. 개발 매니페스트 대비 신규/변경 행만 export.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEV_MANIFEST=""
OUT_DIR="${HOME}/tmp"
TS="$(date '+%Y-%m-%d_%H%M%S')"

usage() {
  cat <<'EOF'
sync_export.sh — 운영: 개발 매니페스트 대비 신규/변경 행만 export

USAGE
  scripts/sync_export.sh --dev-manifest FILE [--project DIR] [--out DIR]

옵션:
  --dev-manifest FILE   scripts/sync_manifest.sh 로 개발에서 생성해 반입한 파일 (필수)
  --project DIR         레포 루트 (기본: 이 스크립트 상위)
  --out DIR             출력 디렉터리 (기본: ~/tmp)

DB 6테이블은 CSV로, data/raw 변경 파일은 raw_files.tar.gz로 묶는다.
출력: citec-kb-incr-<TS>.tar.gz (변경분이 있을 때만 생성)
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --dev-manifest) DEV_MANIFEST="${2:-}"; shift 2 ;;
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$DEV_MANIFEST" && -f "$DEV_MANIFEST" ]] || { echo "ERROR: --dev-manifest 파일 필요" >&2; exit 1; }

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"
TABLES=(documents document_sections chunks checkitems issue_frames failure_buckets)

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "[sync_export] 운영 매니페스트 생성" >&2
"${SCRIPT_DIR}/sync_manifest.sh" --project "$PROJECT_DIR" --out "${STAGING}/ops-manifest.tsv.gz"

echo "[sync_export] diff 계산" >&2
python3 "${SCRIPT_DIR}/sync_diff.py" \
  --dev-manifest "$DEV_MANIFEST" \
  --ops-manifest "${STAGING}/ops-manifest.tsv.gz" \
  --out-dir "${STAGING}/diff" > "${STAGING}/diff_counts.json"
cat "${STAGING}/diff_counts.json" >&2

mkdir -p "${STAGING}/bundle"
HAS_ANY=false
for tbl in "${TABLES[@]}"; do
  ids_file="${STAGING}/diff/${tbl}.ids"
  [[ -f "$ids_file" ]] || continue
  HAS_ANY=true
  n=$(wc -l < "$ids_file")
  echo "[sync_export] ${tbl}: ${n}건 export" >&2
  {
    echo "CREATE TEMP TABLE _sync_ids (id text);"
    echo '\copy _sync_ids FROM STDIN WITH (FORMAT csv)'
    cat "$ids_file"
    echo '\.'
    echo "\\copy (SELECT tbl.* FROM ${tbl} tbl JOIN _sync_ids s ON tbl.id = s.id) TO STDOUT WITH (FORMAT csv, HEADER)"
  } | docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
        psql -q -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" \
        > "${STAGING}/bundle/${tbl}.csv"
done

RAW_IDS_FILE="${STAGING}/diff/raw_files.ids"
if [[ -f "$RAW_IDS_FILE" ]]; then
  HAS_ANY=true
  n=$(wc -l < "$RAW_IDS_FILE")
  echo "[sync_export] raw_files: ${n}건 tar" >&2
  tar czf "${STAGING}/bundle/raw_files.tar.gz" -C "${PROJECT_DIR}/data/raw" -T "$RAW_IDS_FILE"
fi

if ! $HAS_ANY; then
  echo "[sync_export] 변경분 없음 — 번들 생성 안 함" >&2
  exit 0
fi

{
  echo "# citec-kb incremental sync export"
  echo "created=$(date -Iseconds)"
  echo "host=$(hostname)"
  echo "dev_manifest=$(basename "$DEV_MANIFEST")"
  cat "${STAGING}/diff_counts.json"
} > "${STAGING}/bundle/manifest.txt"

mkdir -p "$OUT_DIR"
OUT_TGZ="${OUT_DIR}/citec-kb-incr-${TS}.tar.gz"
(cd "$STAGING" && tar czf "$OUT_TGZ" bundle/)
echo "[sync_export] 완료: ${OUT_TGZ}" >&2
```

- [ ] **Step 2: 실행 권한 부여**

Run: `chmod +x /home/citec/dev/citec-kb/scripts/sync_export.sh`

- [ ] **Step 3: 빈 매니페스트로 "변경 없음" 경로 확인**

빈 매니페스트는 개발 쪽에 아무 것도 없다는 뜻이므로 운영의 모든 행 + 모든 raw 파일(약 5000개)이
신규로 잡힌다. `raw_files.tar.gz` 생성까지 포함하면 다소 걸릴 수 있다 — 이 스텝의 목적은 속도가
아니라 "빈 매니페스트 → 전체가 diff에 잡힘 → 번들 생성됨" 경로가 에러 없이 끝까지 도는지 확인하는
것이다.

Run:
```bash
cd /home/citec/dev/citec-kb
printf '' | gzip -c > /tmp/claude-1000/empty-dev-manifest.tsv.gz
scripts/sync_export.sh --dev-manifest /tmp/claude-1000/empty-dev-manifest.tsv.gz \
  --out /tmp/claude-1000/incr-out
ls /tmp/claude-1000/incr-out
```
Expected: `citec-kb-incr-*.tar.gz` 파일 생성됨.

Run: `tar tzf /tmp/claude-1000/incr-out/citec-kb-incr-*.tar.gz`
Expected: `bundle/`, `bundle/manifest.txt`, 데이터가 있는 테이블 수만큼의 `bundle/<table>.csv`,
그리고 `bundle/raw_files.tar.gz`

- [ ] **Step 4: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add scripts/sync_export.sh
git commit -m "feat(sync): add sync_export.sh for ops-side diff export"
```

---

### Task 4: `sync_apply.sh` — 개발: 단일 트랜잭션 적용 + 재임베딩

**Files:**
- Create: `scripts/sync_apply.sh`

- [ ] **Step 1: 스크립트 작성**

`scripts/sync_apply.sh`:

```bash
#!/usr/bin/env bash
# sync_apply.sh — 개발에서 실행. incr 번들을 단일 트랜잭션으로 적용 후 재임베딩 트리거.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUNDLE=""
NO_REEMBED=false

usage() {
  cat <<'EOF'
sync_apply.sh — 개발: incr 번들 적용 (DB 단일 트랜잭션 + raw 파일 반영) + 재임베딩

USAGE
  scripts/sync_apply.sh BUNDLE.tar.gz [--project DIR] [--no-reembed]

옵션:
  --project DIR    레포 루트 (기본: 이 스크립트 상위)
  --no-reembed     적용 후 app.embed.cli 자동 실행 생략

DB 6테이블은 단일 트랜잭션으로, data/raw 변경 파일(raw_files.tar.gz가 있으면)은 별도로
data/raw 밑에 풀어 반영한다 (파일 복사는 멱등이라 트랜잭션 롤백 대상이 아님).
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT_DIR="${2:-}"; shift 2 ;;
    --no-reembed) NO_REEMBED=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) BUNDLE="$1"; shift ;;
  esac
done

[[ -n "$BUNDLE" && -f "$BUNDLE" ]] || { echo "ERROR: BUNDLE.tar.gz 인자 필요" >&2; usage; exit 1; }

PG_USER="${POSTGRES_USER:-citec}"
PG_DB="${POSTGRES_DB:-citec_knowledge}"

STAGING="$(mktemp -d)"
SQL_SCRIPT="$(mktemp)"
trap 'rm -rf "$STAGING"; rm -f "$SQL_SCRIPT"' EXIT

tar xzf "$BUNDLE" -C "$STAGING"
BUNDLE_DIR="${STAGING}/bundle"
[[ -d "$BUNDLE_DIR" ]] || { echo "ERROR: 번들 구조 이상 (bundle/ 없음)" >&2; exit 1; }

count_query="SELECT 'documents='||count(*) FROM documents
   UNION ALL SELECT 'chunks='||count(*) FROM chunks WHERE is_active
   UNION ALL SELECT 'checkitems='||count(*) FROM checkitems
   UNION ALL SELECT 'issue_frames='||count(*) FROM issue_frames
   UNION ALL SELECT 'failure_buckets='||count(*) FROM failure_buckets;"

echo "[sync_apply] 적용 전 건수" >&2
docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
  psql -U "$PG_USER" -d "$PG_DB" -t -A -c "$count_query" >&2

{
  if [[ -f "${BUNDLE_DIR}/documents.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_documents (LIKE documents INCLUDING DEFAULTS);
SQL
    echo '\copy stg_documents FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/documents.csv"
    echo '\.'
    cat <<'SQL'
UPDATE chunks SET is_active = false
  WHERE document_id IN (SELECT id FROM stg_documents)
    AND document_id IN (SELECT id FROM documents);
DELETE FROM document_sections
  WHERE document_id IN (SELECT id FROM stg_documents)
    AND document_id IN (SELECT id FROM documents);
INSERT INTO documents (
  id, source_id, source_type, external_id, title, body_md, metadata,
  content_hash, version, status, source_uri, lang, evidence_grade,
  environment, domain, work_type, path_l2, path_l3, ingested_at,
  created_at, updated_at
)
SELECT
  id, source_id, source_type, external_id, title, body_md, metadata,
  content_hash, version, status, source_uri, lang, evidence_grade,
  environment, domain, work_type, path_l2, path_l3, ingested_at,
  created_at, updated_at
FROM stg_documents
ON CONFLICT (id) DO UPDATE SET
  source_id = EXCLUDED.source_id,
  source_type = EXCLUDED.source_type,
  external_id = EXCLUDED.external_id,
  title = EXCLUDED.title,
  body_md = EXCLUDED.body_md,
  metadata = EXCLUDED.metadata,
  content_hash = EXCLUDED.content_hash,
  version = EXCLUDED.version,
  status = EXCLUDED.status,
  source_uri = EXCLUDED.source_uri,
  lang = EXCLUDED.lang,
  evidence_grade = EXCLUDED.evidence_grade,
  environment = EXCLUDED.environment,
  domain = EXCLUDED.domain,
  work_type = EXCLUDED.work_type,
  path_l2 = EXCLUDED.path_l2,
  path_l3 = EXCLUDED.path_l3,
  ingested_at = EXCLUDED.ingested_at,
  updated_at = EXCLUDED.updated_at;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/document_sections.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_document_sections (LIKE document_sections INCLUDING DEFAULTS);
SQL
    echo '\copy stg_document_sections FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/document_sections.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO document_sections (id, document_id, heading_path, level, body_md, token_count, ordinal)
SELECT id, document_id, heading_path, level, body_md, token_count, ordinal
FROM stg_document_sections
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  heading_path = EXCLUDED.heading_path,
  level = EXCLUDED.level,
  body_md = EXCLUDED.body_md,
  token_count = EXCLUDED.token_count,
  ordinal = EXCLUDED.ordinal;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/chunks.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_chunks (LIKE chunks INCLUDING DEFAULTS);
SQL
    echo '\copy stg_chunks FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/chunks.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO chunks (id, document_id, section_id, ordinal, text, header_context, token_count, tsv, is_active, created_at)
SELECT id, document_id, section_id, ordinal, text, header_context, token_count, tsv, is_active, created_at
FROM stg_chunks
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  section_id = EXCLUDED.section_id,
  ordinal = EXCLUDED.ordinal,
  text = EXCLUDED.text,
  header_context = EXCLUDED.header_context,
  token_count = EXCLUDED.token_count,
  tsv = EXCLUDED.tsv,
  is_active = EXCLUDED.is_active,
  created_at = EXCLUDED.created_at;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/checkitems.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_checkitems (LIKE checkitems INCLUDING DEFAULTS);
SQL
    echo '\copy stg_checkitems FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/checkitems.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO checkitems (
  id, code, lang, area, category, category_1, subcategory, subject,
  check_method, check_criteria, check_result, risk_if_vulnerable,
  remediation, raw, tsv, document_id, created_at
)
SELECT
  id, code, lang, area, category, category_1, subcategory, subject,
  check_method, check_criteria, check_result, risk_if_vulnerable,
  remediation, raw, tsv, document_id, created_at
FROM stg_checkitems
ON CONFLICT (id) DO UPDATE SET
  code = EXCLUDED.code,
  lang = EXCLUDED.lang,
  area = EXCLUDED.area,
  category = EXCLUDED.category,
  category_1 = EXCLUDED.category_1,
  subcategory = EXCLUDED.subcategory,
  subject = EXCLUDED.subject,
  check_method = EXCLUDED.check_method,
  check_criteria = EXCLUDED.check_criteria,
  check_result = EXCLUDED.check_result,
  risk_if_vulnerable = EXCLUDED.risk_if_vulnerable,
  remediation = EXCLUDED.remediation,
  raw = EXCLUDED.raw,
  tsv = EXCLUDED.tsv,
  document_id = EXCLUDED.document_id;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/issue_frames.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_issue_frames (LIKE issue_frames INCLUDING DEFAULTS);
SQL
    echo '\copy stg_issue_frames FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/issue_frames.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO issue_frames (
  id, document_id, symptom, root_cause, resolution, workaround,
  components, environment, commands, quality, raw_extract,
  created_at, updated_at
)
SELECT
  id, document_id, symptom, root_cause, resolution, workaround,
  components, environment, commands, quality, raw_extract,
  created_at, updated_at
FROM stg_issue_frames
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  symptom = EXCLUDED.symptom,
  root_cause = EXCLUDED.root_cause,
  resolution = EXCLUDED.resolution,
  workaround = EXCLUDED.workaround,
  components = EXCLUDED.components,
  environment = EXCLUDED.environment,
  commands = EXCLUDED.commands,
  quality = EXCLUDED.quality,
  raw_extract = EXCLUDED.raw_extract,
  updated_at = EXCLUDED.updated_at;
SQL
  fi

  if [[ -f "${BUNDLE_DIR}/failure_buckets.csv" ]]; then
    cat <<'SQL'
CREATE TEMP TABLE stg_failure_buckets (LIKE failure_buckets INCLUDING DEFAULTS);
SQL
    echo '\copy stg_failure_buckets FROM STDIN WITH (FORMAT csv, HEADER)'
    cat "${BUNDLE_DIR}/failure_buckets.csv"
    echo '\.'
    cat <<'SQL'
INSERT INTO failure_buckets (
  id, document_id, bucket_name, protocol, symptom,
  discriminating_signals, counter_signals, root_cause, recommended_action,
  confidence, support_count, counter_count, evidence_grade, created_by,
  created_at, updated_at
)
SELECT
  id, document_id, bucket_name, protocol, symptom,
  discriminating_signals, counter_signals, root_cause, recommended_action,
  confidence, support_count, counter_count, evidence_grade, created_by,
  created_at, updated_at
FROM stg_failure_buckets
ON CONFLICT (id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  bucket_name = EXCLUDED.bucket_name,
  protocol = EXCLUDED.protocol,
  symptom = EXCLUDED.symptom,
  discriminating_signals = EXCLUDED.discriminating_signals,
  counter_signals = EXCLUDED.counter_signals,
  root_cause = EXCLUDED.root_cause,
  recommended_action = EXCLUDED.recommended_action,
  confidence = EXCLUDED.confidence,
  support_count = EXCLUDED.support_count,
  counter_count = EXCLUDED.counter_count,
  evidence_grade = EXCLUDED.evidence_grade,
  created_by = EXCLUDED.created_by,
  updated_at = EXCLUDED.updated_at;
SQL
  fi
} > "$SQL_SCRIPT"

echo "[sync_apply] 트랜잭션 적용 시작 (단일 트랜잭션, 실패 시 전체 롤백)" >&2
docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
  psql -q -1 -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" < "$SQL_SCRIPT"
echo "[sync_apply] 트랜잭션 적용 완료" >&2

echo "[sync_apply] 적용 후 건수" >&2
docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T postgres \
  psql -U "$PG_USER" -d "$PG_DB" -t -A -c "$count_query" >&2

if [[ -f "${BUNDLE_DIR}/raw_files.tar.gz" ]]; then
  echo "[sync_apply] raw_files 반영" >&2
  mkdir -p "${PROJECT_DIR}/data/raw"
  tar xzf "${BUNDLE_DIR}/raw_files.tar.gz" -C "${PROJECT_DIR}/data/raw"
  n=$(tar tzf "${BUNDLE_DIR}/raw_files.tar.gz" | grep -c -v '/$' || true)
  echo "[sync_apply] raw_files 반영 완료 (${n}건)" >&2
fi

if ! $NO_REEMBED; then
  echo "[sync_apply] 재임베딩 트리거 (app.embed.cli)" >&2
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T api \
    python -m app.embed.cli
fi

echo "[sync_apply] 완료" >&2
```

- [ ] **Step 2: 실행 권한 부여**

Run: `chmod +x /home/citec/dev/citec-kb/scripts/sync_apply.sh`

- [ ] **Step 3: `--help` 로 인자 파싱 오류 없는지 확인**

Run: `/home/citec/dev/citec-kb/scripts/sync_apply.sh --help`
Expected: usage 텍스트 출력, exit code 0

- [ ] **Step 4: 커밋**

```bash
cd /home/citec/dev/citec-kb
git add scripts/sync_apply.sh
git commit -m "feat(sync): add sync_apply.sh for single-transaction incremental apply + reembed"
```

---

### Task 5: End-to-end 검증 (실제 실행 중인 개발 스택 대상)

이 태스크는 자동화된 테스트가 아니라, 실제 데이터를 건드리기 전에 3개 스크립트가 왕복으로 맞물려
동작하는지 확인하는 수동 검증 절차다. **운영 서버가 아직 없으므로 개발 스택 자체를 "운영" 역할로
빌려 써서 왕복 검증**한다 — `sync_manifest.sh`/`sync_export.sh`/`sync_apply.sh`는 모두 host를
구분하지 않고 현재 접속된 DB에 대해서만 동작하므로, 같은 DB를 대상으로 두 번 실행해도 로직 검증에는
문제없다.

**Files:** 없음 (검증 전용, 코드 변경 없음)

- [ ] **Step 1: 검증용 테스트 문서 삽입**

Run:
```bash
cd /home/citec/dev/citec-kb
docker compose exec -T postgres psql -U citec -d citec_knowledge <<'SQL'
INSERT INTO documents (id, source_type, external_id, title, body_md, content_hash, status)
VALUES ('sync-test-doc-1', 'sync_test', 'sync-test-1', 'Sync 검증용 문서', '# 검증\n본문입니다.', 'hash-v1', 'active');
SQL
```
Expected: `INSERT 0 1`

- [ ] **Step 1.5: 검증용 raw 테스트 파일 추가**

Run:
```bash
mkdir -p /home/citec/dev/citec-kb/data/raw/sync_test
echo "sync 검증용 파일 v1" > /home/citec/dev/citec-kb/data/raw/sync_test/probe.txt
```

- [ ] **Step 2: "before" 매니페스트 생성 (아직 변경 전 상태 = 개발 쪽 매니페스트 역할)**

Run:
```bash
scripts/sync_manifest.sh --out /tmp/claude-1000/e2e-dev-manifest.tsv.gz
zcat /tmp/claude-1000/e2e-dev-manifest.tsv.gz | grep sync-test-doc-1
zcat /tmp/claude-1000/e2e-dev-manifest.tsv.gz | grep sync_test/probe.txt
```
Expected: `documents	sync-test-doc-1	<32자리 md5>` 한 줄, `raw_files	sync_test/probe.txt	<64자리 sha256>` 한 줄

- [ ] **Step 3: 테스트 문서와 테스트 파일을 변경 (변경 시뮬레이션)**

Run:
```bash
docker compose exec -T postgres psql -U citec -d citec_knowledge <<'SQL'
UPDATE documents
SET body_md = '# 검증\n수정된 본문입니다.', content_hash = 'hash-v2', updated_at = now()
WHERE id = 'sync-test-doc-1';
SQL
echo "sync 검증용 파일 v2 (수정됨)" > /home/citec/dev/citec-kb/data/raw/sync_test/probe.txt
echo "신규 파일" > /home/citec/dev/citec-kb/data/raw/sync_test/probe_new.txt
```
Expected: `UPDATE 1`

- [ ] **Step 4: export 실행 — 변경된 문서만 diff에 잡히는지 확인**

Run:
```bash
scripts/sync_export.sh --dev-manifest /tmp/claude-1000/e2e-dev-manifest.tsv.gz \
  --out /tmp/claude-1000/e2e-incr
tar tzf /tmp/claude-1000/e2e-incr/citec-kb-incr-*.tar.gz
```
Expected: `bundle/manifest.txt`, `bundle/documents.csv` (최소) 포함. `bundle/manifest.txt`에
`"documents": <N>` — N은 최소 1 (sync-test-doc-1 포함, 다른 실제 운영 변경 문서가 있으면 더 많을 수
있음).

Run: `grep sync-test-doc-1 <(tar xzfO /tmp/claude-1000/e2e-incr/citec-kb-incr-*.tar.gz bundle/documents.csv)`
Expected: 한 줄 매치 (CSV 안에 테스트 문서가 포함됨)

Run:
```bash
tar xzfO /tmp/claude-1000/e2e-incr/citec-kb-incr-*.tar.gz bundle/raw_files.tar.gz > /tmp/claude-1000/raw_files_check.tar.gz
tar tzf /tmp/claude-1000/raw_files_check.tar.gz | grep sync_test
```
Expected: `sync_test/probe.txt`와 `sync_test/probe_new.txt` 둘 다 포함 (수정된 파일 + 신규 파일).
`sync_test`가 아닌 다른 무변경 raw 파일은 포함되지 않아야 함(운영 환경에 실제로 변경된 다른 파일이
없다면 diff 건수는 딱 이 2개 + 위 documents 변경분과 일치).

- [ ] **Step 5: apply 실행 — 트랜잭션 적용 + 재임베딩 확인**

Run:
```bash
scripts/sync_apply.sh /tmp/claude-1000/e2e-incr/citec-kb-incr-*.tar.gz
```
Expected: "적용 전 건수"/"적용 후 건수" 로그, `app.embed.cli` 출력에 `"errors": 0` 포함.
마지막 줄 `[sync_apply] 완료`.

Run:
```bash
docker compose exec -T postgres psql -U citec -d citec_knowledge -t -A \
  -c "SELECT body_md, content_hash FROM documents WHERE id = 'sync-test-doc-1';"
cat /home/citec/dev/citec-kb/data/raw/sync_test/probe.txt
cat /home/citec/dev/citec-kb/data/raw/sync_test/probe_new.txt
```
Expected: `content_hash`가 `hash-v2`로 반영되어 있음 (같은 DB를 운영/개발 양쪽 역할로 썼으므로
당연히 이미 최신 상태였겠지만, 트랜잭션이 에러 없이 끝까지 실행됐다는 걸 확인하는 것이 목적).
`probe.txt`는 "sync 검증용 파일 v2 (수정됨)", `probe_new.txt`는 "신규 파일" 내용이어야 함(마찬가지로
같은 파일시스템을 왕복했을 뿐이지만, `tar xzf`가 에러 없이 끝까지 실행됐다는 걸 확인하는 목적).

- [ ] **Step 6: 검증용 데이터 정리**

Run:
```bash
docker compose exec -T postgres psql -U citec -d citec_knowledge <<'SQL'
DELETE FROM chunks WHERE document_id = 'sync-test-doc-1';
DELETE FROM document_sections WHERE document_id = 'sync-test-doc-1';
DELETE FROM documents WHERE id = 'sync-test-doc-1';
SQL
rm -rf /home/citec/dev/citec-kb/data/raw/sync_test
rm -rf /tmp/claude-1000/e2e-dev-manifest.tsv.gz /tmp/claude-1000/e2e-incr /tmp/claude-1000/incr-out \
  /tmp/claude-1000/empty-dev-manifest.tsv.gz /tmp/claude-1000/citec-kb-manifest-test.tsv.gz \
  /tmp/claude-1000/raw_files_check.tar.gz
```
Expected: `DELETE` 3건, 임시 파일 및 `data/raw/sync_test/` 정리 완료.

- [ ] **Step 7: 검증 결과를 커밋 메시지로 기록 (코드 변경 없음, 정보성 커밋 생략 가능)**

이 태스크는 코드 변경이 없으므로 커밋하지 않는다. Step 1-6이 모두 기대한 대로 동작했다면
Task 1-4의 구현이 왕복 검증을 통과한 것이다.

---

## 완료 기준

- [ ] `python3 -m pytest scripts/tests/test_sync_diff.py -v` 전체 통과 (9 passed)
- [ ] Task 5의 e2e 검증 Step 1-6이 모두 기대 출력과 일치 (DB 문서 + raw 파일 왕복 모두 포함)
- [ ] `scripts/sync_manifest.sh`, `scripts/sync_export.sh`, `scripts/sync_apply.sh` 모두
      실행 권한(`+x`)이 있고 `--help`로 usage가 출력됨
- [ ] `raw_files`가 DB 6테이블과 동일한 diff 파이프라인(`sync_diff.py`)을 거치며, 변경 없는
      파일은 `raw_files.tar.gz`에 포함되지 않음
