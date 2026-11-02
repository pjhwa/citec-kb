# 동의어 후보 마이닝 런북 (2026-11-02)

모든 운영 명령은 사용자가 실행한다. 후보는 검증을 통과한 것만 `proposed`로 저장되고, `approve` 전에는 검색에 영향이 없다 (개발 전용 플래그 제외, §3).

설계: `docs/superpowers/specs/2026-11-02-synonym-candidate-mining-design.md`

## 사전 확인 (운영, 중요)

1. **운영 `.env`의 `AUTH_MODE`가 `off`가 아니어야 한다.** 쓰기 엔드포인트(`import`/`approve`/`reject`/`disable`)는 `senior`/`admin` 역할을 요구하지만, `AUTH_MODE=off`(docker-compose·`.env` 기본값)에서는 인증이 꺼져 있어 이 역할 게이트가 아무 의미가 없다 (누구나 승인 가능). 운영 `.env`를 직접 확인한다:
   ```bash
   grep -n '^AUTH_MODE' .env
   docker compose exec -T api printenv AUTH_MODE
   ```
   `off`이면 승인 API를 열어 두지 말고(내부망 한정 등) 인증 모드부터 정한다. `apikey` 등으로 켠 경우 아래 curl에는 `-H "X-API-Key: $KEY"`(또는 `Authorization: Bearer`)가 필요하고 키의 역할이 senior/admin이어야 한다.
2. 마이너 CLI는 api 컨테이너 안에서 돈다. 출력 파일(`--out`)은 컨테이너 안 경로이므로 컨테이너를 재생성(`docker compose up -d api` 등)하면 사라질 수 있다. 끝나면 바로 호스트로 복사한다 (§2).

## 0. 배포 (운영, 사용자)
```bash
./scripts/git_pull_deploy.sh      # alembic 20261102_0017 적용 포함 (api 기동 시 자동 upgrade head)
docker compose exec -T api python -m alembic current   # 20261102_0017 (head)
```

> **주의 (롤백 순서)**: `git_pull_deploy.sh`의 자동 롤백은 코드만 되돌리고 DB는 되돌리지 않는다. 0017 적용 후 코드를 이전 커밋으로 롤백하면 api 기동 시 `alembic upgrade head`가 "Can't locate revision"으로 실패해 api가 올라오지 않는다. 0017을 지나 롤백해야 하면 **코드를 되돌리기 전에** 새 코드가 떠 있는 상태에서 `docker compose exec -T api python -m alembic downgrade 20261014_0016`을 먼저 실행한다. 마이그레이션 자체가 실패하면 api가 시작되지 않아 자동 롤백이 동작한다.

## 1. 소규모 시험 (운영, dry-run — DB 쓰기 없음)

`--commit`이 없으면 DB에 쓰지 않고 JSON 파일만 만든다. LLM(Fabrix) 호출은 일어난다.

```bash
./scripts/synonym_mine.sh mine --since-years 2 --limit-tickets 300 --batch-size 100 \
  --out /tmp/syn-trial1.json
```

- stdout은 **JSON 객체 1개**: `{"run_id": …, "stats": {…}, "commit": null}`. 진행 로그는 stderr.
- `--run-id`를 주지 않으면 `--out` 경로에서 파생된다. 같은 `--out`으로 다시 실행하면 이어서 진행(재개)한다.
- 종료코드: `0` 정상, `2` 설정/입출력 오류(LLM 미설정, 쓰기 불가, import 파일 형식 오류), `3` **부분 실행**(`llm_error`/`proc_error`/`parse_fail` > 0).

결과 확인 (후보 내용은 컨테이너 안의 `--out` 파일에 있다):
```bash
docker compose exec -T api cat /tmp/syn-trial1.json | head -80
docker compose exec -T api cat /tmp/syn-trial1.json | jq '.stats'        # jq가 호스트에 있을 때
docker compose exec -T api cat /tmp/syn-trial1.json | jq -r '.items[].terms | join(" | ")'
```
확인할 것: `stats`의 accepted 수, `rej_*` 분포, `llm_error`/`proc_error`/`parse_fail`, 후보 용어가 상식적인지.
후보가 거의 없고 `rej_evidence`가 크면 한 티켓에 두 표현이 같이 나오지 않는 것이다 → `--min-terms-per-ticket 1`(허용값 1 또는 2, 기본 2)로 재시도. 재시도할 때는 다른 `--out`을 쓴다 (같은 `--out`이면 이전 체크포인트를 재사용한다).

주요 옵션 (`mine --help`): `--since-years`(기본 2), `--limit-tickets`, `--batch-size`(기본 100), `--max-batches`, `--run-id`, `--min-terms-per-ticket {1,2}`, `--df-min`(기본 3), `--df-max`(기본 1500, **2000 미만**이어야 함 — DF는 2000에서 잘려 센다), `--out`(필수), `--commit`, `--allow-partial`.

## 2. 전체 실행 (운영)
```bash
./scripts/synonym_mine.sh mine --since-years 2 --batch-size 100 --out /tmp/syn-full1.json
echo "exit=$?"
docker compose cp api:/tmp/syn-full1.json /tmp/syn-full1.json     # 컨테이너 밖으로 보관
```
- 종료코드 `3`(부분 실행)이면 실패한 배치는 체크포인트(`/tmp/syn-full1.json.progress.jsonl`)에 저장되지 않았다. **같은 명령을 그대로 다시 실행**하면 완료된 배치는 건너뛰고 실패한 배치만 다시 호출한다. 체크포인트는 배치의 티켓 ID 집합 해시(`ids_hash`)로 키가 잡혀서, 그 사이 티켓이 바뀐 배치는 자동으로 다시 처리된다.
- 한 번에 끝내고 싶지 않으면 `--max-batches N`으로 끊어 가며 같은 `--out`으로 반복 실행한다 (끊으면 stderr에 경고 출력).
- `--commit`을 함께 주면 통과 후보를 `proposed`로 DB에 넣는다. 부분 실행이면 `--commit`은 **건너뛴다**(stderr 경고, stdout의 `commit`은 `null`). 부분 결과라도 넣고 싶을 때만 `--allow-partial`을 추가한다. 권장 흐름은 dry-run 후 내용을 확인하고 §4에서 `import`하는 것이다.

## 3. 개발 서버에서 게이트 검증 (개발, 어시스턴트와 함께)

1. 운영에서 가져온 파일을 개발 api 컨테이너로 넣고 개발 DB에 import:
   ```bash
   docker compose cp /tmp/syn-full1.json api:/tmp/syn-full1.json
   ./scripts/synonym_mine.sh import --file /tmp/syn-full1.json     # stdout: {"inserted":…,"skipped":…,"invalid":…}
   ```
2. 개발 `.env`에 `SEARCH_SYNONYMS_INCLUDE_PROPOSED=1` 추가 (승인 전 `proposed` 후보까지 검색에 반영하는 **개발 전용** 플래그. 운영에는 쓰지 않는다). api는 `.env`를 `env_file`로 읽으므로 **`docker compose up -d api`로 컨테이너를 재생성해야 환경변수가 적용**된다 (`restart`만으로는 `.env` 변경이 반영되지 않는다). 런타임 그룹 캐시 TTL은 `SEARCH_SYNONYMS_TTL_S`(기본 60초)이므로, 이후 DB 상태를 바꾸면 TTL이 지나야(또는 approve/reject/disable을 받은 워커 프로세스 하나는 즉시, 나머지 워커는 TTL 이내) 반영된다. 게이트 측정은 import 직후 TTL 이상 기다린 뒤 시작한다.
3. 게이트 실행. 기준선은 `artifacts/citec-kb-retrieval-fix-20261028/` 의 최신 측정인 `after-latency-fix.json`이다 (명령 형식은 같은 디렉터리 README.md와 동일, 약 18분 소요):
   ```bash
   NO_COLOR=1 python3 -u scripts/verify/eval_retrieval_gate.py run --base-url http://localhost:8573 \
     --holdout artifacts/citec-graph-final/20261014-f06-holdout-freeze/evaluation/holdout-2026-10-14.json \
     --evalset artifacts/citec-kb-retrieval-fix-20261028/evalset-2026-10-28.json \
     --label after-synonyms --out artifacts/citec-kb-retrieval-fix-20261028/after-synonyms.json
   python3 scripts/verify/eval_retrieval_gate.py compare \
     artifacts/citec-kb-retrieval-fix-20261028/after-latency-fix.json \
     artifacts/citec-kb-retrieval-fix-20261028/after-synonyms.json      # 종료코드 1 = 승인 금지
   ```
   (측정 전 dev api가 현재 코드로 떠 있어야 한다. 일부 군만 다시 재려면 `--only-groups …`.)
4. 통과 조건: holdout recall@10 후퇴 없음(on/off 모두), pass→fail 회귀 없음, p50/p95 지연 증가 없음. 게이트는 지연을 판정하지 않으므로 아래 §5의 curl 측정으로 별도 기록한다 (dev 지연은 절대값이 아니라 전/후 비교용).
5. 후퇴가 있으면 후보를 절반씩 나눠 원인 그룹을 찾는다 (개발 DB에서 `reject`/`disable`, TTL 대기 후 재측정).

## 4. 승인 (운영, 사용자)

게이트를 통과한 뒤에만 한다. `BASE`는 운영 API 주소(호스트 포트 8573), 인증이 켜져 있으면 모든 호출에 `-H "X-API-Key: $KEY"`를 붙인다 (senior/admin 키).

```bash
# 승인 전 지연 기준선 측정 (§5)을 먼저 해 둔다.

# 1) 후보 적재 (dry-run 파일을 import; 이미 있는 terms_key는 건너뜀). 
docker compose cp /tmp/syn-full1.json api:/tmp/syn-full1.json     # 컨테이너가 재생성되었을 때만
./scripts/synonym_mine.sh import --file /tmp/syn-full1.json
#    (REST로 넣으려면 POST $BASE/v1/lexicon/synonym-candidates/import — 아래 한도 참고)

# 2) 목록 확인
curl -s "$BASE/v1/lexicon/synonym-candidates?status=proposed&limit=200" -H "X-API-Key: $KEY" | jq .

# 3) 승인 / 반려 — 본문은 생략 가능(빈 본문 허용), note는 선택(<=1000자)
curl -s -X POST "$BASE/v1/lexicon/synonym-candidates/<id>/approve" -H "X-API-Key: $KEY"
curl -s -X POST "$BASE/v1/lexicon/synonym-candidates/<id>/approve" -H "X-API-Key: $KEY" \
  -H 'content-type: application/json' -d '{"note":"gate pass"}'
curl -s -X POST "$BASE/v1/lexicon/synonym-candidates/<id>/reject" -H "X-API-Key: $KEY"
```
- 상태 전이: `approve`는 proposed/disabled에서, `reject`는 proposed에서, `disable`은 approved에서만 가능 (그 외 409, 없는 id 404).
- 권한: `import`/`approve`/`reject`/`disable`은 senior/admin. 목록 조회(GET)는 역할 제한 없음.
- import 한도(REST): 항목 <= 2000개, 항목당 terms <= 20개이고 각 용어 <= 100자, `evidence`는 JSON 직렬화 8192자 이하, `run_id` <= 64자, `model` <= 128자. 한도를 넘으면 422. CLI `import`가 보내는 건 마이너 출력이라 보통 문제없다.
- 승인 후 반영: 요청을 받은 워커 프로세스 하나는 즉시, 나머지 워커는 캐시 TTL(`SEARCH_SYNONYMS_TTL_S`, 기본 60초) 안에 반영된다.

## 5. 승인 전/후 지연 측정 (운영)

`/v1/search` p50/p95를 승인 **전**과 **후**에 같은 질의·같은 방식으로 잰다 (후는 TTL 경과 후). 질의는 `artifacts/citec-kb-retrieval-fix-20261028/evalset-2026-10-28.json`의 질의 문구만 재사용한 것이다 (정답 ID는 쓰지 않는다).

```bash
BASE=http://localhost:8573          # 운영 api (호스트 포트)
HDR=()                              # 인증이 켜져 있으면: HDR=(-H "X-API-Key: $KEY")
OUT=/tmp/search-lat-before.txt      # 승인 후에는 /tmp/search-lat-after.txt
QUERIES=(
  "Redis timeout"
  "Linux OOM"
  "SCP 장애"
  "NetApp 성능 지연"
  "Kubernetes pod 재시작"
  "방화벽 정책 변경"
  "Oracle RAC 장애"
  "VMware 가상머신 지연"
  "Linux Out of Memory"
  "메모리 누수 pacemaker"
)
: > "$OUT"
for round in 1 2 3; do
  for q in "${QUERIES[@]}"; do
    body=$(jq -nc --arg q "$q" '{q:$q, top_k:10, filters:{status:"active"}, multi_query:true}')
    curl -s -o /dev/null -w '%{time_total}\n' -X POST "$BASE/v1/search" \
      "${HDR[@]}" -H 'content-type: application/json' -d "$body" >> "$OUT"
  done
done
sort -n "$OUT" | awk '{a[NR]=$1} END{printf "n=%d p50=%.3fs p95=%.3fs\n", NR, a[int((NR+1)/2)], a[int(NR*0.95+0.999)]}'
```
- 같은 질의가 캐시되어 첫 호출만 느릴 수 있으므로 3라운드를 돌려 합산한다 (n=30). 판정은 전/후 p50, p95 비교로 한다.
- 승인 후 p95가 눈에 띄게 늘면 §6의 `disable`로 의심 그룹부터 끈다.

## 6. 롤백

- 그룹 단위 (즉시): `POST $BASE/v1/lexicon/synonym-candidates/<id>/disable` (approved -> disabled). 반영은 요청을 받은 워커 프로세스 하나는 즉시, 나머지 워커는 TTL 이내. 전체를 끄려면 승인한 그룹을 모두 `disable`한다. DB 오류 시 검색은 자동으로 코드 그룹(`SYNONYM_GROUPS`)만 쓴다.
- 스키마: `docker compose exec -T api python -m alembic downgrade 20261014_0016` — `search_synonym_candidate` 테이블이 삭제된다. 코드에 정의된 동의어 그룹은 영향 없다 (런타임 병합은 DB 실패 시 코드 그룹으로 폴백). 다시 올리려면 `alembic upgrade head`.
- 개발 flag 롤백: `.env`에서 `SEARCH_SYNONYMS_INCLUDE_PROPOSED` 제거 후 `docker compose up -d api`.
