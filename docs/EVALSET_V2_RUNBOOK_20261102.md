# 평가셋 v2 런북 (2026-11-02)

실사용 질의 + 합성 질의를 풀링 -> LLM 보조 -> 사람 O/△/X로 판정해 60~80 질의 평가셋을 만들고, 게이트로 전/후 차이를 잰다.

- 설계: `docs/superpowers/specs/2026-11-02-evalset-v2-design.md`
- 코드: `apps/api/app/evalv2/` (CLI: `python -m app.evalv2.cli <cmd>`), 게이트: `scripts/verify/eval_retrieval_gate.py`
- 산출물 위치: `artifacts/citec-eval-v2/`

## 누가 무엇을 하나

| 단계 | 실행자 | 대상 | 비고 |
|---|---|---|---|
| A. 운영 로그 추출 | **사용자만** | 운영 DB (읽기 전용 SELECT) | 어시스턴트는 운영에 접속하지 않는다 |
| B. 질의 준비·풀링·LLM 판정·시트 생성 | 어시스턴트(또는 사용자) | **개발 서버만** | 개발 LLM(OpenRouter)을 쓴다. 운영 코퍼스는 외부 LLM으로 보내지 않는다 |
| C. 브라우저 검수 | **사용자** | 로컬 브라우저 | 최종 판정은 사람이다 |
| D. 라벨 import | 어시스턴트 | 개발 서버 | 평가셋 JSON 생성 |
| E. 게이트 측정 | 어시스턴트 | 개발 서버 REST | 읽기 전용 |

## Step A. 운영 로그 추출 (사용자, 운영, 읽기 전용)

`mcp_access_log`(`apps/api/app/db/models.py` `McpAccessLog`: `created_at`, `tool`, `path`, `query_summary` 등)에서 `/v1/search` 호출의 질의만 뽑는다. `query_summary`는 `q=<질의> [다른키=값 …]` 형태이고 500자에서 잘린다(`app/audit/mcp_access.py`). SELECT만 실행되며 쓰기는 없다.

```bash
cd <운영 citec-kb 디렉터리>
docker compose exec -T postgres psql -U citec -d citec_knowledge -c \
  "\copy (select to_char(created_at,'YYYY-MM-DD') as d, tool, count(*) as n, query_summary from mcp_access_log where path='/v1/search' and query_summary like 'q=%' group by 1,2,4 order by n desc, d) to stdout with (format csv, delimiter E'\t')" > /tmp/evalv2-real.tsv
```

컬럼 순서는 `날짜 TAB tool TAB 횟수 TAB query_summary`이고 **헤더 줄은 없다**. 같은 날 같은 도구·같은 질의는 한 줄로 묶여 횟수(n)가 된다.

### TSV 점검 (사용자)

```bash
wc -l /tmp/evalv2-real.tsv     # 줄 수. 보고해 주세요
head -3 /tmp/evalv2-real.tsv   # 헤더가 없고 4칸(탭 구분)인지 확인
less /tmp/evalv2-real.tsv      # 훑어보며 민감한 줄을 찾는다
```

- 고객명, 계정, 담당자 이름, 내부 호스트·IP, 연락처 등이 들어간 줄은 **편집기로 지운다** (`sed -i '/문자열/d' /tmp/evalv2-real.tsv`). 사람 이름은 코드로 찾을 수 없으므로 눈으로 확인해야 한다.
- 코드가 자동으로 걸러내는 것(`prep-real`): 잘린 질의, 5자 미만, 300자 초과, 식별자 형태만 있는 질의(`CITECTS-2502` 등), 기존 홀드아웃·evalset과 같은 문구, 완전 중복(횟수 합산).
- 코드가 **지우지는 않고 표시만 하는** 민감 힌트(`hints`): `email`, `ip`, `phone`(휴대폰·대표번호·국제번호 포함), `rrn`(주민번호 형태), `url`, `long_digits`(8자리 이상 숫자). 힌트가 붙은 질의는 `prep-real` 실행 시 stderr에 경고가 나오며, Step B에서 JSON을 직접 열어 지우거나 바꾼다.
- 테스트 호출이 섞여 있을 수 있다. 의미 없는 질의(`test`, 하네스 문구 등)도 같이 지운다.

### 개발 서버로 옮기기

운영 서버 -> (사용자 PC 경유 scp 등) -> 개발 서버 `/tmp/evalv2-real.tsv`. 내부 정책이 허용하는 방법으로 옮긴다. TSV는 **이미 민감 줄을 지운 파일**이어야 한다.

## Step B. 개발 서버 작업 (모두 개발 서버에서만)

모든 CLI는 api 컨테이너 안에서 돈다 (동의어 마이닝과 같은 방식). 입력/출력 파일은 `docker compose cp`로 옮긴다. 컨테이너 안 경로는 컨테이너를 재생성하면 사라지므로 끝날 때마다 호스트로 꺼낸다.

```bash
cd /home/citec/dev/citec-kb
export EV=/tmp/evalv2                       # 컨테이너 안 작업 디렉터리
docker compose exec -T api mkdir -p $EV

# 입력 넣기: 운영 로그 TSV, 제외용 홀드아웃·기존 evalset
docker compose cp /tmp/evalv2-real.tsv api:$EV/real.tsv
docker compose cp artifacts/citec-graph-final/20261014-f06-holdout-freeze/evaluation/holdout-2026-10-14.json api:$EV/holdout.json
docker compose cp artifacts/citec-kb-retrieval-fix-20261028/evalset-2026-10-28.json api:$EV/evalset-old.json
```

> `--evalset`은 여러 번 줄 수 있다. `--holdout`/`--evalset`은 같은 문구의 질의를 제외(prep-real)하거나 새로 만들지 않게(gen-synth) 하는 데 쓴다.

### B-1. 실사용 질의 정리 (`prep-real`)

```bash
docker compose exec -T api python -m app.evalv2.cli prep-real \
  --tsv $EV/real.tsv --holdout $EV/holdout.json --evalset $EV/evalset-old.json --out $EV/real.json
```

- stdout: `{"kept": N, "flagged_for_review": M, "stats": {truncated, too_short, too_long, identifier_only, known_case, kept}}`
- 기대: 실사용 30~40건 목표지만 로그에 따라 적을 수 있다 (스펙 §9). 적으면 합성을 늘린다 (`--n` 조정).
- `flagged_for_review`가 0이 아니면 stderr에 `WARNING`이 나온다. 파일을 꺼내 힌트 항목을 확인·수정한다:

```bash
docker compose cp api:$EV/real.json ./real.json      # 열어서 hints 가 비어 있지 않은 항목 편집/삭제
docker compose cp ./real.json api:$EV/real.json
```

  편집할 때는 `items` 배열에서 해당 항목을 통째로 지우거나 `q`를 중립 문구로 바꾼다 (`hints`는 참고 표시일 뿐이므로 지운 뒤 비워도 된다).

### B-2. 합성 질의 3종 (`gen-synth` x3)

슬라이스는 `vocab`(한·영·약어 표기 차이), `pointer`(매뉴얼·문서 탐색형, confluence_map 대상), `symptom`(증상 서술, support_history 대상). 슬라이스당 `--n 15`(문서 15건), 문서당 최대 2질의(`--per-doc 2`)를 권장한다. 생성기는 제목 겹침률 0.5 초과 질의를 버리므로 실제 질의 수는 `--n`보다 적을 수 있다.

```bash
for s in vocab pointer symptom; do
  docker compose exec -T api python -m app.evalv2.cli gen-synth \
    --slice $s --n 15 --per-doc 2 \
    --holdout $EV/holdout.json --evalset $EV/evalset-old.json --out $EV/synth-$s.json
done
```

- stdout: `{"docs": 문서수, "queries": 질의수}`. 모델명과 `--seed`(기본 `evalv2`)가 출력 JSON에 기록된다 (재현성).
- 슬라이스에 `queries`가 0이면 필터가 너무 엄격하거나 LLM이 실패한 것이다. `--n`을 늘려 재시도하고 보고한다.
- LLM이 설정돼 있지 않으면 `LLM not configured`로 종료된다.

### B-3. 풀링 (`pool`)

질의마다 `multi_query` on/off 두 설정의 상위 10(`--top-n`)을 합쳐 후보 풀(`--max-pool 15`)을 만든다. 합성 질의는 출처 문서(seed)가 항상 후보에 들어간다.

```bash
docker compose exec -T api python -m app.evalv2.cli pool \
  --queries $EV/real.json $EV/synth-vocab.json $EV/synth-pointer.json $EV/synth-symptom.json \
  --base-url http://localhost:8000 --out $EV/pooled.json
```

- `--base-url`은 컨테이너 안에서 본 api 주소(`http://localhost:8000`, 기본값). 호스트에서 돌릴 때는 `http://localhost:8573`.
- 진행은 stderr에 `r-001: 13 candidates`처럼 질의당 한 줄, 끝에 `pooled N/M queries (K failed)`.
- 스모크 기준 질의당 후보 약 13개. 시간은 개발 서버 검색 지연(multi_query on 시 p50 약 17초, 위 baseline-A 참고)에 비례하므로 질의 1건당 대략 30초, **약 70질의면 30~40분** 정도 잡는다.
- 한 질의가 실패해도 중단하지 않고 해당 질의에 `pool_error`를 기록한다 (트러블슈팅 참고).

### B-4. LLM 보조 판정 (`judge`)

후보별로 관련도 0/1/2와 근거 문장을 제안받고, 근거 문장이 본문에 실제로 있는지 코드가 검증한다 (없으면 `unverified`).

```bash
docker compose exec -T api python -m app.evalv2.cli judge --pooled $EV/pooled.json --out $EV/judged.json
echo "exit=$?"
```

- 질의당 LLM 호출 약 2회 (후보를 나눠 보냄). 70질의면 약 140회 호출 -- 개발 LLM(OpenRouter) 응답 속도에 따라 수십 분.
- 종료 코드: `0` 정상, `3` 일부 질의 `llm_error` (아래 트러블슈팅), 설정 오류는 `LLM not configured`.
- 후보의 `llm.status`: `ok`(판정됨) / `unverified`(근거가 본문에 없어 등급 무시) / `invalid`·`missing`(응답 형식 문제) / `empty`(빈 응답, 아래 참고) / `llm_error`.

### B-5. 검수 시트 (`sheet`)

```bash
docker compose exec -T api python -m app.evalv2.cli sheet --judged $EV/judged.json --out $EV/review.html
# 선택: --doc-base http://<개발서버 web 주소>  를 주면 후보마다 "원문 열기" 링크가 생긴다 (http/https만 허용)

# 결과를 호스트로 꺼낸다 (artifacts 에는 judged.json 도 같이 보관: 이후 import-labels 입력)
mkdir -p artifacts/citec-eval-v2
docker compose cp api:$EV/review.html artifacts/citec-eval-v2/review.html
docker compose cp api:$EV/judged.json artifacts/citec-eval-v2/judged.json
docker compose cp api:$EV/pooled.json artifacts/citec-eval-v2/pooled.json
```

`review.html`은 서버 없이 쓰는 정적 파일 한 장이다. 질의당 후보는 최대 8개로 줄어 표시된다 (seed 문서 > LLM 관련 후보 > 사람이 봐야 할 미검증 후보 > 풀 상위 3).
`judged.json`·`pooled.json`에는 개발 코퍼스 문서 제목과 질의가 들어 있다 (실사용 질의는 민감 정보를 지운 뒤의 것). 저장소에 올릴지는 `git status`로 확인하고 정책에 맞게 결정한다.

## Step C. 사람 검수 (사용자, 약 1~2시간 / 70질의)

1. `artifacts/citec-eval-v2/review.html`을 브라우저로 연다 (파일 더블클릭 또는 `xdg-open`). 서버·네트워크가 필요 없다.
2. 상단에 **검수자** 이름과 **검수일**(`YYYY-MM-DD`, 기본은 오늘)을 입력한다. 비우면 import가 거부된다.
3. 후보마다 판정한다.
   - **O**: 이 문서가 질의에 직접 답한다 (-> `expect`, 게이트의 정답).
   - **△**: 관련은 있으나 부분적이다 (-> `also_ok`, 게이트는 무시. 정답 처리 안 됨).
   - **X**: 무관하다 (-> 제외).
   - 표시하지 않은 후보는 무시된다. 질의에 **아무것도 표시하지 않으면** "미검수"로 빠진다.
4. **앵커링 편향 주의**: 먼저 문서 자체(제목·본문)를 보고 스스로 판정한 뒤에 LLM 등급·근거를 훑어본다. LLM 힌트를 먼저 보면 그대로 따라가기 쉽다. LLM 판정은 후보 제안일 뿐 최종 판정은 사람이다. 근거 문장에 "검증됨" 표시가 있어도 판정이 맞다는 뜻이 아니라 본문에 그 문장이 존재한다는 뜻일 뿐이다.
5. 다 끝나면 **review-labels.json 내려받기** 버튼을 눌러 `review-labels.json`을 받는다. 중간에 닫아도 진행은 브라우저에 저장된다 (같은 브라우저·같은 `review.html`에서 이어서).

### 처음 한 번 확인할 것 (브라우저)

- [ ] △ 버튼을 누르면 **주황색**으로 바뀐다 (O 초록, X 빨강). 안 바뀌면 CSS 문제이므로 알려 주세요.
- [ ] 몇 개 표시한 뒤 **새로고침**해도 표시가 남아 있다 (localStorage).
- [ ] 내려받은 `review-labels.json`을 열면 `reviewer`, `reviewed_on`, `labels`(질의 id -> {문서 id -> "O"/"△"/"X"})가 있다.

받은 파일을 `artifacts/citec-eval-v2/review-labels.json`으로 저장해 알려 주면 된다.

## Step D. 라벨 import (개발)

```bash
docker compose cp artifacts/citec-eval-v2/judged.json api:$EV/judged.json
docker compose cp artifacts/citec-eval-v2/review-labels.json api:$EV/review-labels.json
docker compose exec -T api python -m app.evalv2.cli import-labels \
  --judged $EV/judged.json --review $EV/review-labels.json \
  --evalset-id evalset-v2-20261102 --out-dir $EV/out
docker compose cp api:$EV/out/. artifacts/citec-eval-v2/
```

생성 파일 (`artifacts/citec-eval-v2/`): `evalset-v2-dev.json`, `evalset-v2-frozen.json`, `report.json`.

- stdout: `{"kept", "no_answer", "unreviewed", "dev", "frozen"}`.
  - **kept**: O가 하나 이상 있어 평가셋 케이스가 된 질의.
  - **no_answer**: 검수는 했지만 O가 없는 질의 (모두 X거나 △뿐). 케이스에서 제외된다 -- 후보에 정답이 없다는 뜻이므로 많으면 풀링/질의 품질을 점검한다.
  - **unreviewed**: 아무 표시도 하지 않은 질의. 제외된다.
- 분할: 질의 문구(정규화)의 해시로 `dev`/`frozen`이 갈린다. 같은 질의는 항상 같은 쪽이다 (대략 반반). `frozen` 파일에는 동결 시각(`frozen_at_utc`)이 기록된다.
- 군 이름: `v2_real`, `v2_vocab`, `v2_pointer`, `v2_symptom`. 케이스에는 `source`, `split`, `reviewed_on`, `also_ok`가 들어 있다.
- 종료 코드 `2`: 검수 파일이 잘못됨 (검수자·검수일 비어 있음, 모르는 질의/후보 id, 이상한 표시). 메시지가 stderr에 나온다.

저장 위치는 `artifacts/citec-eval-v2/` 하나이며, 한 번 만든 파일은 같은 이름으로 덮어쓰지 말고 새 `--evalset-id`/새 폴더(예: 날짜 접미사)로 만든다 (전/후 비교가 같은 평가셋을 쓰도록).

## Step E. 게이트로 측정

게이트는 REST만 호출하는 읽기 전용이다. 측정 전에 개발 api를 새 코드로 재시작한다 (`docker compose restart api`: 소스는 bind mount지만 프로세스가 자동 재로드되지 않는다).

### 측정 (`run`)

**튜닝·개선 확인은 `dev`로만** 한다. `frozen`은 릴리스 판정 때만 돌리고, 그 결과를 보고 튜닝하지 않는다.

```bash
cd /home/citec/dev/citec-kb
# 변경 전 (기준선)
NO_COLOR=1 python3 -u scripts/verify/eval_retrieval_gate.py run --base-url http://localhost:8573 \
  --evalset artifacts/citec-eval-v2/evalset-v2-dev.json \
  --label v2dev-before --out artifacts/citec-eval-v2/v2dev-before.json
# ... 변경 적용, 필요 시 docker compose restart api ...
# 변경 후 (반드시 같은 --evalset 파일)
NO_COLOR=1 python3 -u scripts/verify/eval_retrieval_gate.py run --base-url http://localhost:8573 \
  --evalset artifacts/citec-eval-v2/evalset-v2-dev.json \
  --label v2dev-after --out artifacts/citec-eval-v2/v2dev-after.json
```

- 기존 홀드아웃·evalset도 함께 보려면 `--holdout …/holdout-2026-10-14.json`과 `--evalset`을 더 붙인다(기존 README 방식: `artifacts/citec-kb-retrieval-fix-20261028/README.md`). 홀드아웃을 빼면 v2 군만 잰다. 그 경우 `--evalset`은 파일 하나다.
- 일부 군만: `--only-groups v2_symptom,v2_vocab` (기존 `--out`에 병합, 홀드아웃은 건너뜀).
- 소요 시간은 개발 서버 지연에 비례한다 (기존 evalset 50건 + 홀드아웃이 약 18분이었다).
- 릴리스 판정 때만: 같은 방식으로 `--evalset artifacts/citec-eval-v2/evalset-v2-frozen.json`.

### 비교 (`compare`)

```bash
python3 scripts/verify/eval_retrieval_gate.py compare \
  artifacts/citec-eval-v2/v2dev-before.json artifacts/citec-eval-v2/v2dev-after.json
echo "exit=$?"     # 1 = 머지 금지, 0 = 통과
```

**before/after는 같은 평가셋(같은 케이스 id 집합)으로 측정해야 한다.** v2 군의 케이스 id 집합이 다르거나(한쪽에만 있는 군 포함) 하면 게이트가 **실패**한다 ("같은 평가셋으로 다시 측정할 것"). 평가셋을 바꿨다면 before도 다시 재야 한다.

### v2 블록 읽는 법

```
== v2 군 (통계 판정: recall@10 차이의 95% 구간이 전부 음수일 때만 실패) ==
군                    n      metric       Δ              95% 구간  판정
```

- `n`: 군의 공통 케이스 수. `n < 8`이면 "표본 부족 -- 판정하지 않음"(안내만, 실패 아님). 공통 케이스가 하나도 없는 군도 건너뛴다.
- `Δ`: after - before의 평균 차이. recall@10(상위 10 안에 `expect`가 있으면 1)과 MRR 두 줄이 나온다. **MRR은 참고용**이고 판정은 recall@10만 쓴다.
- `95% 구간`: 질의 단위 페어 부트스트랩 구간.
- 판정: 구간 전체가 0 미만이면 **유의한 하락**(게이트 실패), 전체가 0 초과면 **유의한 개선**, 0을 포함하면 **차이 불분명**(통과, 그러나 "좋아졌다"고 말할 수 없음).
- 케이스 단위 pass -> fail은 v2 군에서는 "단건 pass→fail(참고, 판정에 쓰지 않음): N건"으로만 나온다. 기존 군(홀드아웃·기존 evalset)은 예전처럼 단건 회귀가 실패다.

### 읽기 예시 (가짜 숫자)

```
v2_symptom         20   recall@10  +0.100    [-0.050, +0.250]  차이 불분명
v2_symptom         20         mrr  +0.040    [-0.030, +0.110]  차이 불분명
v2_vocab           18   recall@10  +0.222    [+0.056, +0.389]  유의한 개선
v2_pointer         16   recall@10  -0.188    [-0.375, -0.063]  유의한 하락
v2_real             6  표본 부족(n<8) — 판정하지 않음
```

- `v2_symptom`: 평균은 올랐지만 구간이 0을 포함 -> 개선이라고 말할 수 없다. 통과.
- `v2_vocab`: 구간이 전부 양수 -> 개선이라고 말할 수 있다.
- `v2_pointer`: 구간이 전부 음수 -> **게이트 실패**(종료코드 1). 하락 원인을 보고 고친다. 케이스를 골라 평가셋을 바꾸는 식으로 해결하지 않는다.
- `v2_real`: 표본이 너무 적어 판정 대상이 아니다 (실사용 질의가 적을 때 흔함).

## 알려진 한계 (스펙 §9)

- 풀링은 현재 시스템이 올린 문서 위주로 후보를 만들어 **절대 recall은 부풀려질 수 있다.** 전/후 상대 비교만 유효하다.
- 합성 질의는 제목 겹침률 필터로 편향을 완화했을 뿐 완전히 제거하지 못했다.
- 실사용 질의 수는 운영 로그에 달려 있고 테스트 호출이 섞여 있어 기대보다 적을 수 있다. 그 경우 합성 비중을 늘린다(`--n`).
- LLM 판정은 후보 제안일 뿐이다. 최종 판정은 사람의 O/△/X다.
- 케이스가 60~80건이라 군당 n이 작다. 구간이 넓으면 "차이 불분명"이 흔하다 -- 그것은 정상이다.
- `also_ok`(△)는 게이트가 무시한다.

## 트러블슈팅

| 증상 | 원인/조치 |
|---|---|
| `judge`에서 후보 `llm.status`가 `empty` | LLM이 빈 응답을 돌려줌 (콘텐츠 필터가 차단하는 경우 등). 코드가 한 번 재시도한 뒤 `empty`로 기록한다. 해당 후보는 LLM 힌트 없이 사람이 문서 자체로 판정하는 대상으로 시트에 우선 표시된다. 별도 조치 불필요 |
| `judge` 종료 코드 `3`, stderr에 `<id>: llm_error: …` | 해당 질의의 LLM 호출이 예외(타임아웃·네트워크·키)로 실패. 그 질의 후보는 `llm_error`로 기록돼 힌트 없이 시트에 나온다. 원인(개발 LLM 설정/네트워크) 해결 후 `judge`를 다시 돌려 전체를 새로 만든다 (이어 하기 없음) |
| `judge`/`gen-synth`가 `LLM not configured` | 개발 서버 `.env`의 `LLM_BACKEND`가 `none`. 개발 LLM 설정 후 재시도 |
| `pool`에서 `<id>: pool failed: …`, 항목에 `pool_error` | 두 설정(on/off) 검색이 모두 실패했고 seed도 없음 (api 다운·타임아웃). 한쪽만 실패하면 경고만 남기고 계속한다. api 상태 확인 후 `pool`을 다시 돌린다. `pool_error` 질의는 후보가 없어 검수 대상에서 사실상 빠진다 |
| `import-labels` 종료 코드 `2` | 검수자/검수일 누락, 잘못된 id/표시. stderr 메시지대로 `review-labels.json`을 고치거나 시트에서 다시 내려받는다 |
| `compare`가 "케이스 구성 불일치"로 실패 | before/after가 다른 평가셋으로 측정됨. 같은 `--evalset` 파일로 둘 다 다시 측정 |
| 합성 질의가 0건 | 겹침률 필터(0.5)가 엄격하거나 LLM 실패. `--n` 증가, 다른 `--seed` 시도 후 보고 |
