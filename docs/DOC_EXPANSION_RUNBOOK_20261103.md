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
