# 문서 확장(검색용 설명문) 런북 — 개발 서버

- 작성일: 2026-11-03
- 스펙: `docs/superpowers/specs/2026-11-03-doc-expansion-design.md`, 계획: `docs/superpowers/plans/2026-11-03-doc-expansion.md`
- 범위: 개발 서버 전용(OpenRouter). 운영 적용은 측정 결과를 본 뒤 별도 결정한다.
- 모든 명령은 저장소 루트에서 실행한다. `A='docker compose exec -T api'`

## 1. 마이그레이션 (먼저!)

플래그를 켜기 전에 반드시 먼저 적용한다. 테이블이 없는데 `SEARCH_DOC_EXPANSION`을 켜면 검색이 500을 낸다.

```bash
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic current   # 20261103_0018 (head)
```

## 2. 대상 집계 (LLM 호출 없음)

```bash
docker compose exec -T api python -m app.docexp.cli count
```

## 3. 파일럿: 무작위 200건

```bash
docker compose exec -T api python -m app.docexp.cli sample --n 200 --seed 1 --workers 4 \
  --out /tmp/docexp-sample.json --commit
```

- 사람이 훑어볼 파일: `/tmp/docexp-sample.json` (external_id, title, rule, summary, keywords, grounded).
- 품질 확인 체크리스트: 요약이 본문에 충실한가 / 키워드가 현업이 쓸 법한 표현인가 / 근거 있는 키워드(grounded) 비율 / 폐기율(stdout의 `stats.discarded`) / 오류율(`llm_error`, `parse_fail`, `empty_response`).
- 종료 코드 3 = 일부 실패. stdout의 `stats`를 확인하고 같은 명령을 다시 실행해도 된다(`run`은 이미 저장된 문서를 건너뛴다).

## 4. 전체 실행 (파일럿 품질 확인 후)

```bash
docker compose exec -T -d api sh -c 'python -m app.docexp.cli run --commit --workers 4 --out /tmp/docexp-run.json > /tmp/docexp-run.stdout 2>/tmp/docexp-run.stderr'
docker compose exec -T api python -m app.docexp.cli stats
```

- 재개: 같은 명령을 다시 실행한다(이미 저장된 문서와 해시가 같으면 건너뜀).
- 문서 본문이 바뀌면(재수집) 저장된 해시가 달라져 검색에서 무시되고, `run`이 다시 생성한다.

## 5. 측정 (같은 평가셋 `artifacts/citec-eval-v2/evalset-v2-all.json`, 기준선 `v2all-baseline.json`)

```bash
for MODE in grounded all; do
  SEARCH_DOC_EXPANSION=$MODE docker compose up -d api   # compose 가 변수를 api 컨테이너에 전달하는지 확인
  NO_COLOR=1 python3 -u scripts/verify/eval_retrieval_gate.py run --base-url http://localhost:8573 \
    --evalset artifacts/citec-eval-v2/evalset-v2-all.json \
    --label v2all-$MODE --out artifacts/citec-eval-v2/v2all-$MODE.json
done
python3 scripts/verify/eval_retrieval_gate.py compare \
  --before artifacts/citec-eval-v2/v2all-baseline.json --after artifacts/citec-eval-v2/v2all-all.json
```

- `compare` 인자 이름은 `eval_retrieval_gate.py compare --help`로 확인한다.
- 기존 홀드아웃·evalset 게이트도 같은 모드로 실행하고, p50/p95 지연을 별도로 비교한다.
- 판정: `v2_pointer`/`v2_vocab` recall@10 개선, `v2_real` 악화 없음, 기존 게이트 통과, 지연 악화 없음.

## 6. 롤백

```bash
# 플래그 제거 후 재시작 (기본값 off)
docker compose up -d api
# 데이터까지 제거하려면
docker compose exec -T api alembic downgrade 20261102_0017
```

## 7. 한계

- 개발 서버 한정이며, 운영 코퍼스와의 차이로 효과·지연은 운영에서 읽기 전용 측정으로 확정해야 한다.
- 확장문은 랭킹 신호일 뿐 근거가 아니다. 응답에 노출되지 않는다.
- 대상 규칙은 제목 정보량 기준이며 평가셋에 맞춰 바꾸지 않는다.

## 8. 운영에서 생성하고 개발에서 측정 (OpenRouter 크레딧이 없을 때)

운영은 Fabrix를 쓰므로 크레딧 문제가 없다. 운영에서는 **생성만** 하고(플래그는 켜지 않는다), 결과를 JSONL로 내보내 개발 서버에서 가져와 측정한다. 쓰기는 새 테이블 `doc_expansion` 한 곳뿐이고 검색 동작은 바뀌지 않는다(`SEARCH_DOC_EXPANSION` 기본 off). 모든 명령은 사용자가 운영 서버에서 실행한다.

### 8.1 배포와 마이그레이션 (운영)
```bash
cd <운영 저장소>
git pull   # 또는 평소 배포 스크립트
docker compose up -d api   # 코드 마운트 반영
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic current   # 20261103_0018 (head)
```
롤백: `docker compose exec -T api alembic downgrade 20261102_0017` (테이블만 사라진다).

### 8.2 LLM 확인과 소규모 시험 (운영)
```bash
docker compose exec -T api python -m app.docexp.cli count
docker compose exec -T api python -m app.docexp.cli sample --n 30 --seed 1 --workers 2 --out /tmp/docexp-sample.json --commit
```
stdout JSON의 `stats`를 붙여 주세요. Fabrix 필터로 응답이 비는 경우(`empty_response`)는 재시도로 일부 복구되며, `ok` 비율이 낮으면 `--workers 1`로 낮춘다. 종료 코드 4는 401/402/403 연속으로 중단된 것이다.

### 8.3 전체 생성 (운영, 백그라운드)
```bash
docker compose exec -d api sh -c 'python -m app.docexp.cli run --commit --workers 3 --out /tmp/docexp-run.json > /tmp/docexp-run.stdout 2>/tmp/docexp-run.stderr'
docker compose exec -T api python -m app.docexp.cli stats     # 진행 상황
docker compose exec -T api cat /tmp/docexp-run.stdout          # 완료 후 결과
```
종료 코드 3(일부 실패)이면 같은 명령을 다시 실행한다(저장된 문서는 건너뛴다). 컨테이너가 재시작되면 실행이 끊기므로 같은 명령으로 이어서 실행한다.

### 8.4 내보내기와 개발로 옮기기
```bash
# 운영
docker compose exec -T api python -m app.docexp.cli export --out /tmp/docexp-export.jsonl
docker compose cp api:/tmp/docexp-export.jsonl ./docexp-export.jsonl
# 개발 서버로 복사 (scp 등)
scp ./docexp-export.jsonl citec@172.16.8.250:/tmp/docexp-export.jsonl
```
내보낸 파일에는 문서 제목·요약이 들어 있으므로 사내 경로로만 옮긴다.

### 8.5 개발에서 가져오기
```bash
docker compose cp /tmp/docexp-export.jsonl api:/tmp/docexp-export.jsonl
docker compose exec -T api python -m app.docexp.cli import --in /tmp/docexp-export.jsonl            # 드라이런: matched / hash_mismatch 확인
docker compose exec -T api python -m app.docexp.cli import --in /tmp/docexp-export.jsonl --ignore-hash --commit
```
`--ignore-hash`는 개발·운영 본문이 조금 달라도 외부 ID가 같으면 가져온다(평가용 근사). 개발에 남아 있는 OpenRouter 생성분(약 2.6천 건)은 `--skip-existing`을 주지 않으면 운영 결과로 덮어쓴다.

이후 측정은 §5와 같다.
