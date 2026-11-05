# 백업·복구 운영 가이드

DB(Postgres) 덤프와 `data/`를 **호스트에서** 백업한다. api 컨테이너에는 `pg_dump`도 도커 소켓도 없어서, 실행은 호스트의 `scripts/backup_manager.py`가 맡고 admin 화면(백업 섹션)은 그 결과를 읽고 "지금 백업"을 요청만 한다.

## 1. 무엇이 백업되나

백업 한 번 = **세트** 하나 = 디렉터리 하나 (`<BACKUP_DIR>/<YYYYmmdd_HHMMSS>-<scheduled|manual>/`):

| 파일 | 내용 |
|---|---|
| `db.sql.gz` | Postgres 전체 덤프(plain SQL, `--clean --if-exists`, pgvector 확장 포함) |
| `data.tar.gz` | 저장소 `data/` 전체(원본 raw, 시드, 마이닝 결과, 허용 목록 override, 해시된 발급 토큰 …). **`data/backups` 자체는 제외** |
| `SHA256SUMS`, `manifest.json` | 체크섬과 메타데이터. `manifest.json`은 **마지막에** 쓰므로 없으면 미완성 세트로 취급해 집계·복구 대상에서 뺀다 |

**의도적으로 넣지 않는 것:** `.env`와 `config/auth.json`(비밀 — 별도 비밀 저장소에 보관), `models/`(다시 받을 수 있음), Neo4j(`scripts/graph_sync.sh`로 재구축), `logs/`.

기본 저장 위치는 `<repo>/data/backups`(같은 서버 디스크). `BACKUP_DIR`로 `/DATA` 안의 다른 경로로 바꿀 수 있다. **같은 서버 디스크이므로 디스크 장애에는 보호되지 않는다** — 가끔 외부 저장소로 복사해 둔다.

## 2. 보관 정책 — 몇 세트가 쌓이나

기본 **최근 5세트**(`BACKUP_KEEP` 또는 `--keep N`). 새 백업이 성공하면 그 뒤에 가장 오래된 세트부터 지워 5개로 맞춘다(정기·수동 구분 없이 합산). 실패한 백업은 세트를 남기지 않고 기존 세트도 지우지 않는다.

세트 하나의 크기는 거의 `data/`(현재 약 3.3GB 중 압축 후)와 DB 덤프 크기다. 최대 용량 ≈ **세트 크기 × 5**. 첫 백업 후 화면의 "백업 총용량"과 "디스크 사용률"로 실제 값을 확인하고 `BACKUP_KEEP`을 조절한다. 디스크 여유가 부족하면(필요 추정치 = `data/` 크기×1.1 + `BACKUP_MIN_FREE_GB`, 기본 2GB) 백업을 시작하지 않고 실패로 기록한다.

## 3. 호스트 crontab 등록 (운영자, 1회)

```
30 3 * * * /usr/bin/flock -n /tmp/citec-kb-backup.lock /DATA/jooksan.park/citec-kb/scripts/backup_manager.py run >> /DATA/jooksan.park/citec-kb/logs/cron_backup.log 2>&1
* * * * * /DATA/jooksan.park/citec-kb/scripts/backup_manager.py process-requests >> /DATA/jooksan.park/citec-kb/logs/cron_backup_requests.log 2>&1
```

- 03:30은 ingest(02:00) 이후, api 재시작(04:00) 이전이다. ingest가 길어져 `data/`를 쓰는 중이어도 백업은 진행하며(tar의 "파일이 바뀜"은 경고로 기록), 필요하면 시각을 옮긴다.
- 두 번째 줄(1분 주기)이 화면의 "지금 백업 요청"을 집어 간다. 화면의 상태 갱신도 이 줄이 한다. 등록하지 않으면 화면은 "미설정"/"러너 정지 의심"을 표시한다.
- 두 job은 admin 화면의 "크론 작업 상태"에도 자동으로 나타난다(`cron_snapshot.py`).

## 4. admin 화면

- **개요**의 "백업" 타일과 "확인할 일": 최근 성공이 30시간 넘으면 주의, 54시간 넘거나 마지막 시도 실패면 이상, 디스크 여유 20%/10% 미만이면 주의/이상.
- **백업 섹션**: 세트 목록(종류, 시각, 크기), 보관 현황, 디스크 사용률, **지금 백업 요청**(admin 롤, 대기/진행 중이면 중복 요청 불가).
- 화면에서 **삭제·복구는 하지 않는다.** 삭제는 보관 정책이, 복구는 아래 절차가 한다.

## 5. 검증

```bash
scripts/backup_manager.py verify            # 가장 최근 세트
scripts/backup_manager.py verify <세트 ID>
```
sha256 일치, 덤프 완료 표식(`PostgreSQL database dump complete`), tar 목록 전체 읽기를 확인한다. 백업 직후에도 같은 검사를 자동으로 하고, 실패하면 세트를 남기지 않는다.

## 6. 복구 절차 (운영자가 호스트에서)

> DB를 덮어쓰는 작업이다. 먼저 현재 상태를 한 번 더 백업(`backup_manager.py run --kind manual`)하고, 가능하면 별도 DB에 시험 복구해 본다.

```bash
cd /DATA/jooksan.park/citec-kb
SET=<BACKUP_DIR>/<세트 ID>

scripts/backup_manager.py verify <세트 ID>                         # 0) 검증
docker compose stop api worker                                      # 1) 쓰기 중지
gunzip -c $SET/db.sql.gz | docker compose exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'   # 2) DB 복원
tar -xzf $SET/data.tar.gz -C .                                      # 3) data 복원(필요할 때만)
docker compose up -d api worker                                     # 4) 기동 (alembic은 시작 시 자동 적용)
```

- 덤프에 `--clean --if-exists`가 들어 있어 기존 객체를 지우고 다시 만든다. 완전히 빈 DB에 복원하려면 먼저 `DROP DATABASE`/`CREATE DATABASE` 후 `CREATE EXTENSION vector`를 하거나 그대로 실행해도 된다(덤프가 확장을 만든다).
- `data.tar.gz` 복원은 현재 `data/`의 같은 이름 파일을 덮어쓴다. `data/backups`는 건드리지 않는다(아카이브에 없다).
- 복원 후 Neo4j 그래프가 PG와 어긋나면 `scripts/graph_sync.sh`로 다시 맞춘다.

## 7. 검증 상태

- 가짜 덤프 명령으로 세트 생성·검증·회전·미완성 세트 처리·디스크 가드·요청 처리를 테스트한다(`apps/api/tests/test_backup_manager.py`).
- 실제 Postgres(pgvector) 컨테이너에서 같은 형태의 `sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" …'` 명령으로 덤프하고 빈 DB에 복원해 행 수와 확장이 일치함을 확인했다.
- **운영 서버에서의 실제 실행(`docker compose exec -T postgres …`와 crontab 환경)은 아직 확인하지 못했다.** 등록 후 첫 정기 실행 전에 `scripts/backup_manager.py run --kind manual`을 한 번 직접 돌려 로그와 화면을 확인하길 권한다.
