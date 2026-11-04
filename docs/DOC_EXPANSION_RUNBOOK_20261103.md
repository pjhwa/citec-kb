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

## 9. 측정 결과 (2026-11-04): 효과 없음, 운영 미적용

- 조건: 개발 서버, 운영 Fabrix로 생성한 확장 20,669건 중 개발에 있는 문서 15,313건을 `import --ignore-hash`로 적용. 평가셋 `evalset-v2-all.json`(46건). 결과 파일 `artifacts/citec-eval-v2/v2all-{off,grounded,all}.json`.
- recall@10은 `off`/`grounded`/`all` 모두 동일: v2_pointer 0.125, v2_vocab 0.368, v2_real 1.00. 게이트는 통과했으나 이는 회귀가 없다는 뜻이며 개선은 없다.
- v2_real MRR 0.731 → 0.679(grounded) / 0.675(all). 구간이 0을 포함해 통계적으로 불분명하나 1위 정답 일부가 밀렸다. p50 지연은 모드와 무관하게 비슷했다.
- 원인: 정답 문서에 확장 데이터가 있어도(pointer 4건 확인, 해시 일치, 내용 양호) 효과가 없다. 검색이 `plainto_tsquery("simple")`로 질의 단어를 모두 AND로 맞추고 형태소 분석이 없어, 단어 하나(예: "콘솔" vs "console", "바닥·넘어진" vs "전도")가 어긋나면 매칭이 끊긴다. 합성 질의가 정답 단어를 피하도록 만들어져 어휘 간극이 크다.
- vocab 정답의 상당수는 `tech_repo`/`incident_reports`라 대상 규칙(제목 정보량이 낮은 `confluence_map`)에 해당하지 않는다. 규칙은 평가셋에 맞춰 바꾸지 않는다.
- 결정: `SEARCH_DOC_EXPANSION`은 off로 둔다. 운영에는 `doc_expansion` 테이블만 있고 검색 동작은 바뀌지 않는다. 후속 후보: 확장문의 벡터 경로 반영(임베딩 재생성 필요), 확장 분기만 OR 계열 매칭으로 완화(오탐 위험).
- 정리가 필요하면 §6의 롤백(`alembic downgrade 20261102_0017`)으로 테이블을 제거한다.

### 9.1 추가 실험: 확장 분기 OR 매칭 (2026-11-04, 코드는 되돌림)

- 변경: 확장 분기에서 질의 토큰(최대 8개)을 `plainto_tsquery`들의 OR(`||`)로 묶은 쿼리 하나를 사용(`all` 모드). 기존 분기는 앞 4개 변형(대부분 단일 토큰)을 개별 실행하므로 이미 일부 OR이다.
- 결과(`artifacts/citec-eval-v2/v2all-or-all.json`): recall@10 변화 없음(pointer 0.125, vocab 0.368, real 1.00). MRR은 pointer -0.062, vocab -0.035, real -0.091로 하락(구간이 0을 포함). 1위였던 정답이 밀림(r-436 1→8, s-vocab-007 1→3).
- 결론: 렉시컬 경로에서 매칭 방식을 바꿔도 recall이 오르지 않는다. 사용하지 않을 분기라 코드는 커밋하지 않았다. 남은 후보는 확장문의 벡터 경로 반영(임베딩 재생성 필요)이다.

### 9.2 추가 실험: 확장문 임베딩(벡터 경로) (2026-11-04)

- 구현: `doc_expansion_embedding`(마이그레이션 0019), `docexp embed`, `SEARCH_DOC_EXPANSION_VEC=on` + 가중치. 확장 벡터 top-N의 대표 청크를 RRF 세 번째 리스트로 합쳤다. 개발 서버에서 15,335건 임베딩(약 2시간).
- 결과(46건, `artifacts/citec-eval-v2/v2all-vec-{0.5,1.0}.json`): recall@10 변화 없음(pointer 0.125, vocab 0.368, real 1.00). MRR은 pointer -0.062, real -0.044(0.5)/-0.055(1.0), vocab 0/-0.035로 하락(구간은 0 포함). 1위였던 정답이 밀림(r-436 1→6~8, s-pointer-013 1→2). p50 지연은 9.2~9.8초로 기능 off 측정(8.4초)보다 약간 높았다.
- 판정: 사전에 정한 성공 기준(pointer/vocab recall@10 +0.10 또는 새로 top-10에 진입, v2_real MRR 하락 없음) 미달. 실패.
- 종합: 렉시컬(AND/OR)과 벡터 모두 확장문으로 recall을 올리지 못했다. 병목은 확장 신호가 아니라 정답 문서가 후보 풀이나 상위 10위에 들어오지 못하는 다른 지점일 가능성이 크다.

### 9.3 정정과 재측정: 개발 DB의 청크 누락 (2026-11-05)

- 진단: pointer 케이스 8건 중 7건의 정답은 청크가 0개였다. 개발 DB의 `confluence_map` 78,251건 중 44,094건(56%)에 활성 청크가 없고(9/23 이후 생성분, 원본 raw 파일도 없음), 문서 확장 15,335건 중 8,602건도 여기에 속한다. **운영은 `confluence_map` 101,157건 전부 청크가 있다.** 개발 DB는 이 영역에서 운영의 불완전한 사본이며, §9~9.2의 pointer 결과는 개발에서 검증되지 않은 것이었다.
- 조치(실험용, 저장소 미포함 스크립트): 청크가 없는 문서 중 확장이 있는 8,602건에 청크 15,877개를 만들고 임베딩(약 4시간). 같은 평가셋으로 `off`/`grounded`/`all` 재측정(`artifacts/citec-eval-v2/v2bf-*.json`).
- 결과: recall@10 변화 없음(pointer 0.125, vocab 0.421, real 0.947). v2_real MRR은 grounded -0.009, all -0.041(구간이 0 포함). 게이트 통과(회귀 없음).
- 원인 확인(pointer 8건): 청크 복구 후에도 확장 효과가 없다. 1월 3주차 미팅은 FTS 500위 안에 없고(질의 "바닥·넘어진"과 요약 "전도"는 어휘가 다르고 "사고" 하나만 겹침), 위젯 API(007)는 확장이 켜져야 FTS 447위에 겨우 들어온다. 나머지 정답(Pod Evicted 등)은 확장 대상 규칙(제목 정보량)에 해당하지 않아 확장이 없고 청크도 없다.
- 한계: 개발 코퍼스는 여전히 운영과 다르다(청크 없는 문서 약 35천 건 잔존). 운영에서의 효과는 개발 측정으로 확정되지 않는다. 벡터 경로 실험(§9.2)은 청크 누락 상태에서 이뤄졌으므로 같은 한계가 있다.
- 결론: 렉시컬 신호로 어휘 간극("바닥 넘어진" vs "전도")을 메우지 못한다. 운영 효과 확인은 운영 읽기 전용 측정으로만 가능하다.
