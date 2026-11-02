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

슬라이스와 표본 대상:
- `vocab`: `tech_repo` 문서 중 제목/본문 앞부분에 영문 용어(라틴 문자 2자 이상)가 있는 것. 제목과 반대 언어·표기(한글 풀어쓰기 <-> 영문·약어)로 질의를 만든다.
- `pointer`: `confluence_map` 문서 (본문 20자 이상이면 됨).
- `symptom`: `support_history` 문서. 증상 서술 질의.

그 밖의 표본 조건: `active` 문서, 제목 8자 이상, 본문 200자 이상(pointer만 20자 이상). 코드는 문서를 `--seed`로 결정적으로 섞어 **2n개를 뽑고**, 질의가 채택된 문서가 n개가 되면 멈춘다 (LLM/필터가 거부한 문서는 건너뛴다). 문서당 최대 `--per-doc`(2)개 질의. 제목·본문 앞부분과의 겹침률이 높은 질의는 버려지므로 실제 질의 수는 대개 `--n x 2`보다 적다. 슬라이스당 `--n 15`를 권장한다.

```bash
for s in vocab pointer symptom; do
  docker compose exec -T api python -m app.evalv2.cli gen-synth \
    --slice $s --n 15 --per-doc 2 \
    --holdout $EV/holdout.json --evalset $EV/evalset-old.json --out $EV/synth-$s.json
done
echo "exit=$?"
```

- 질의 id는 `s-<slice>-<seed 해시 4자>-###` (예: `s-vocab-1a2b-001`). **`--seed`를 바꿔 다시 돌리면 id가 달라져** 다른 실행과 id가 충돌하지 않는다. 같은 슬라이스를 두 번 생성해 합칠 때는 반드시 서로 다른 `--seed`를 쓴다 (`pool`은 id 중복이면 종료 코드 2로 거부한다). 같은 `--seed`·같은 코퍼스면 같은 문서가 뽑힌다 (재현성). 모델명과 seed는 출력 JSON에 기록된다.
- `--out`이 이미 있으면 덮어쓴다 (`gen-synth`에는 `--resume`/`--force`가 없다).
- stdout: `{"docs": 표본문서수, "queries": 질의수, "llm_failures": LLM 실패 횟수}`.
- **종료 코드 3** = 일부만 성공: 표본 문서가 있는데 질의가 0건이거나 LLM 호출이 한 번이라도 실패했을 때 (stderr에 `WARNING`). 파일은 그래도 써진다. 질의가 0건이면 `--n`을 늘리거나 다른 `--seed`로 재시도하고 보고한다.
- LLM이 설정돼 있지 않으면 `LLM not configured`로 종료된다.

### B-3. 풀링 (`pool`)

질의마다 `multi_query` on/off 두 설정의 상위 10(`--top-n`)을 합쳐 후보 풀(`--max-pool 15`)을 만든다. 합성 질의는 출처 문서(seed)가 항상 후보에 들어간다.

```bash
docker compose exec -T api python -m app.evalv2.cli pool \
  --queries $EV/real.json $EV/synth-vocab.json $EV/synth-pointer.json $EV/synth-symptom.json \
  --base-url http://localhost:8000 --out $EV/pooled.json
echo "exit=$?"
```

- `--base-url`은 컨테이너 안에서 본 api 주소(기본 `http://localhost:8000`). 호스트에서 돌릴 때는 `http://localhost:8573`.
- **질의마다 결과를 파일에 바로 쓴다** (중간에 죽어도 거기까지는 남는다). `--out`이 이미 있으면 **거부(종료 코드 2)**한다:
  - `--resume`: 이어서 한다. 기존 파일의 `run_id`가 이번 입력과 같아야 하며(다르면 거부), 이미 풀링된 질의는 `skipped (resume)`으로 건너뛰고 `pool_error`가 난 질의만 다시 한다.
  - `--force`: 덮어쓴다.
- `run_id`는 질의 id+문구에서 계산한 10자리 해시로, 출력 JSON에 기록돼 `pool` -> `judge` -> `sheet`로 이어진다. 입력 질의가 바뀌면 `run_id`가 달라져 `--resume`이 거부된다.
- `--queries` 여러 파일 사이에 **같은 질의 id가 있으면 종료 코드 2**로 거부한다.
- 진행은 stderr에 질의당 한 줄(`r-001: 13 candidates`), 끝에 `pooled N/M queries (K failed) run_id=…`.
- **종료 코드 3** = 한 질의라도 `pool_error`(두 설정 모두 실패, seed도 없음)가 났다. 해당 질의는 후보가 비어 있고 `pool_error`가 기록된다. api 상태를 확인하고 `--resume`으로 그 질의만 다시 돌린다.
- 한쪽 설정만 실패하면 경고만 남기고 다른 쪽 결과로 계속한다.

### B-4. LLM 보조 판정 (`judge`)

후보별로 관련도 0/1/2와 근거 문장을 제안받는다. LLM에는 후보 본문 전체가 아니라 **질의와 관련된 부분 1200자 창**만 보여 준다. 등급이 1 또는 2이면 **창 안에서 글자 그대로 복사한 12자 이상의 인용**을 evidence로 내야 하고, 코드가 그 인용이 창에 실제로 있는지 검증한다 (없으면 `unverified`, 등급 무시).

```bash
docker compose exec -T api python -m app.evalv2.cli judge --pooled $EV/pooled.json --out $EV/judged.json
echo "exit=$?"
```

- `pool`과 같다: 질의마다 파일에 쓰고, `--out`이 있으면 `--resume`(같은 `run_id`만)/`--force` 없이는 거부(종료 코드 2). 질의 id가 중복돼도 종료 코드 2. `run_id`는 pooled.json에서 이어받는다.
- `--resume` 때는 모든 후보가 `ok/unverified/missing/invalid`로 끝난 질의는 건너뛰고, `llm_error`/`empty`가 있는 질의만 다시 판정한다.
- 후보의 `llm.status`: `ok`(판정됨) / `unverified`(인용이 창에 없음) / `missing`·`invalid`(응답 형식 문제) / `empty`(빈 응답) / `llm_error`(호출 예외).
- 끝에 상태별 요약 한 줄이 stderr에 나온다:
  `judged 68/70 queries, llm_error queries: 2; candidates: ok=…, unverified=…, missing=…, invalid=…, empty=…, llm_error=…`
- **종료 코드 3** = `llm_error`가 난 질의가 있거나 `empty`/`llm_error` 후보가 하나라도 있다. `unverified/missing/invalid`만 있는 것은 정상(0)이다 -- 사람이 문서로 판정하는 후보로 시트에 우선 표시된다.
- 설정 오류: `LLM not configured`.

### B-5. 검수 시트 (`sheet`)

```bash
docker compose exec -T api python -m app.evalv2.cli sheet --judged $EV/judged.json \
  --doc-base http://<개발서버>:8572 --out $EV/review.html
# --doc-base 는 선택. 웹 UI(nginx, 기본 포트 8572) 주소이며 http/https 만 허용된다. 생략하면 링크가 없다.

# 결과를 호스트로 꺼낸다 (judged.json 은 import-labels 입력이므로 같이 보관)
mkdir -p artifacts/citec-eval-v2
docker compose cp api:$EV/review.html artifacts/citec-eval-v2/review.html
docker compose cp api:$EV/judged.json artifacts/citec-eval-v2/judged.json
docker compose cp api:$EV/pooled.json artifacts/citec-eval-v2/pooled.json
```

`review.html`은 서버 없이 쓰는 정적 파일 한 장이다.
- 질의당 후보: **seed 문서와 풀 상위 3은 LLM과 무관하게 항상 표시**, 거기에 LLM 관련 후보(등급 1·2)와 사람이 봐야 할 미검증 후보(`unverified/missing/empty/invalid/llm_error`)가 최대 8개까지 더해진다 (질의당 최대 약 12개).
- 후보 순서는 결정적으로 섞여 있어 풀 순위가 드러나지 않는다.
- "원문 열기" 링크: `<doc-base>/doc.html?eid=<external_id>&st=<source_type>`. `apps/web/public/doc.html`을 읽어 확인했다: `eid`와 `st`(source_type, 기본 `support_history`) 쿼리를 받아 `/v1/tickets/<eid>?source_type=<st>`로 조회하고(PISA 코드는 `/v1/checkitems/<eid>`), 실패하면 `/api/wiki/file?path=<st>/<eid>.md`로 폴백한다. 이 경로가 모든 source_type(confluence_map, tech_repo 등)을 여는지는 실제 브라우저로 확인하지 못했다 -- 안 열리면 아래 Step C 확인 항목에 적는다.

> 시간 예상 (개발 서버에서 측정한 값): `gen-synth` 문서당 약 5~10초, `pool` 질의당 후보 약 12개(검색 2회: multi_query on 약 15~20초 + off 약 8초 -> 질의당 약 25~30초), `judge` 질의당 약 10초(후보 11~14개). 70질의 기준으로 합성 생성 5~10분 x 3 슬라이스(전체 약 5~15분), 풀링 약 30~35분, 판정 약 12분. 시트 생성은 몇 초.

`judged.json`·`pooled.json`에는 개발 코퍼스 문서 제목과 질의가 들어 있다 (실사용 질의는 민감 정보를 지운 뒤의 것). 저장소에 올릴지는 `git status`로 확인하고 정책에 맞게 결정한다.

## Step C. 사람 검수 (사용자, 약 1~2시간 / 70질의)

1. `artifacts/citec-eval-v2/review.html`을 브라우저로 연다 (파일 더블클릭 또는 `xdg-open`). 서버·네트워크가 필요 없다 ("원문 열기" 링크만 `--doc-base` 서버가 필요).
2. 상단에 **검수자** 이름과 **검수일**(`YYYY-MM-DD`, 기본은 오늘)을 입력한다. 이름이 비어 있으면 내려받기 버튼이 막힌다(경고 표시).
3. 후보마다 판정한다 (표시를 다시 누르면 해제, 다른 버튼을 누르면 바뀐다).
   - **O**: 이 문서가 질의에 직접 답한다 (-> `expect`, 게이트의 정답).
   - **△**: 관련은 있으나 부분적이다 (-> `also_ok`, 게이트는 무시).
   - **X**: 무관하다 (-> 제외).
   - **표시하지 않은 후보는 "비관련"으로 간주된다** (import 보고서의 `unlabeled_candidates`로 집계). 질의에 **아무것도 표시하지 않으면** "미검수"로 빠진다. **O가 하나도 없는 질의(모두 X, △뿐 포함)는 평가셋에서 제외**된다.
4. **앵커링 편향 방지**: 상단의 **"LLM 힌트 숨기기"가 기본으로 체크**돼 있어 LLM 등급·근거, 검색 순위(arms), seed 표시가 숨겨진다. **먼저 문서 자체(제목·원문)를 보고 스스로 판정**하고, 애매할 때만 체크를 풀어 LLM 힌트를 참고한다 (이 설정은 브라우저에 기억된다). 근거의 "검증됨"은 판정이 맞다는 뜻이 아니라 인용이 본문에 존재한다는 뜻일 뿐이다.
5. 다 끝나면 **review-labels.json 내려받기**를 눌러 `review-labels.json`을 받는다. 중간에 닫아도 진행은 브라우저에 저장된다. 저장 키에 `run_id`와 후보 목록 해시가 들어 있어, **다른 실행/다른 시트를 열면 이전 표시가 섞이지 않고** 빈 상태로 시작한다 (같은 `review.html`을 같은 브라우저에서 열어야 이어서 된다).

### 처음 한 번 확인할 것 (브라우저)

- [ ] △ 버튼을 누르면 **주황색**으로 바뀐다 (O 초록, X 빨강). 안 바뀌면 알려 주세요.
- [ ] 표시한 뒤 **새로고침**해도 표시가 남아 있다 (localStorage).
- [ ] "LLM 힌트 숨기기"를 풀면 LLM 등급·근거·arms가 보이고 켜면 사라진다.
- [ ] 검수자 이름을 비우고 내려받기를 누르면 막힌다. 이름을 넣으면 `review-labels.json`이 내려받아지고 `reviewer`, `reviewed_on`, `labels`(질의 id -> {문서 id -> "O"/"△"/"X"})가 있다.
- [ ] (`--doc-base`를 줬다면) "원문 열기"가 문서를 연다.

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

- stdout: `{"kept", "no_answer", "unreviewed", "unlabeled_candidates", "kept_by_slice", "dev", "frozen"}` (`report.json`에는 dev/frozen 개수를 뺀 나머지가 들어간다).
  - **kept**: O가 하나 이상 있어 평가셋 케이스가 된 질의.
  - **no_answer**: 검수는 했지만 O가 없는 질의 (모두 X거나 **△뿐 -- △만 있는 질의도 버려진다**). 케이스에서 제외된다 -- 후보에 정답이 없다는 뜻이므로 많으면 풀링/질의 품질을 점검한다.
  - **unreviewed**: 아무 표시도 하지 않은 질의. 제외된다.
  - **unlabeled_candidates**: kept 질의에서 표시가 없어 비관련으로 처리된 후보 수 (많을수록 풀링 한계로 recall이 과대평가될 수 있다).
  - **kept_by_slice**: 슬라이스별 kept 수 (`real`/`vocab`/`pointer`/`symptom`). 군당 n이 8 미만이면 게이트가 그 군을 판정하지 않으니 확인한다.
- 분할: 합성 질의는 **seed 문서의 external_id**(없으면 정규화한 질의 문구)의 해시로 `dev`/`frozen`이 갈린다 (대략 반반). 같은 문서에서 나온 질의는 항상 같은 쪽이라 dev와 frozen에 걸치지 않는다. `frozen` 파일에는 동결 시각(`frozen_at_utc`)이 기록되고, **같은 `--out-dir`에 이미 frozen 파일이 있으면 기존 `frozen_at_utc`를 유지**한다 (다시 import해도 동결 시각이 바뀌지 않는다).
- 군 이름: `v2_real`, `v2_vocab`, `v2_pointer`, `v2_symptom`. 케이스에는 `source`, `split`, `reviewed_on`, `also_ok`가 들어 있다.
- 종료 코드 `2`: 검수 파일이 잘못됨 (검수자 비어 있음, 존재하지 않는 날짜, judged의 중복 질의 id, 모르는 질의/후보 id, 이상한 표시). 메시지가 stderr에 나온다.

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

**before/after는 같은 평가셋(같은 케이스 id 집합)으로 측정해야 한다.** v2 군의 케이스 id 집합이 **정확히 같지 않으면**(한쪽에만 있는 군 포함) 게이트가 **종료 코드 1로 실패**한다 ("같은 평가셋으로 다시 측정할 것"). 평가셋을 바꿨다면 before도 다시 재야 한다.

### v2 블록 읽는 법

```
== v2 군 (통계 판정: recall@10 차이의 95% 구간이 전부 음수일 때만 실패) ==
군                    n      metric       Δ              95% 구간  판정
```

- `n`: 군의 공통 케이스 수. `n < 8`이면 "표본 부족 -- 판정하지 않음"(안내만 하고 실패는 아님. 의도된 관대함). 공통 케이스가 하나도 없는 군도 건너뛴다.
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
| `pool`/`judge`가 `already exists; use --resume … or --force`로 종료 코드 2 | 안전장치. 이어서 하려면 `--resume`, 처음부터면 `--force` |
| `--resume`이 `has run_id …, expected …`로 거부 | 입력 질의(id/문구)가 기존 `--out`을 만들 때와 다름. 같은 입력인지 확인하거나 `--force` |
| `pool`이 `duplicate query ids …`로 종료 코드 2 | `--queries` 파일 사이에 질의 id 충돌 (같은 슬라이스를 같은 `--seed`로 두 번 생성한 경우 등). 다른 `--seed`로 다시 생성 |
| `judge` 후보 `llm.status`가 `empty` | LLM이 빈 응답을 돌려줌 (콘텐츠 필터 차단 등). 한 번 재시도 후 `empty`로 기록, 종료 코드 3. `--resume`으로 그 질의만 다시 시도할 수 있고, 계속 비면 LLM 힌트 없이 사람이 문서로 판정하면 된다(시트에 우선 표시됨) |
| `judge` 종료 코드 3, stderr에 `<id>: llm_error: …` | 해당 질의 LLM 호출이 예외(타임아웃·네트워크·키). 원인 해결 후 `judge --resume`으로 그 질의만 재판정 |
| `judge`/`gen-synth`가 `LLM not configured` | 개발 서버 `.env`의 `LLM_BACKEND`가 `none`. 개발 LLM 설정 후 재시도 |
| `gen-synth` 종료 코드 3 / 질의 0건 | LLM 실패(`llm_failures`) 또는 겹침률 필터가 모두 거부. `--n` 증가, 다른 `--seed`로 재시도 후 보고 |
| `pool`에서 `<id>: pool failed: …`, 항목에 `pool_error`, 종료 코드 3 | 두 설정(on/off) 검색이 모두 실패하고 seed도 없음 (api 다운·타임아웃). api 확인 후 `pool --resume` |
| `import-labels` 종료 코드 `2` | 검수자/검수일 누락·잘못된 날짜, 잘못된 id/표시. stderr 메시지대로 고치거나 시트에서 다시 내려받는다 |
| `compare`가 "케이스 구성 불일치"로 실패 | before/after가 다른 평가셋으로 측정됨. 같은 `--evalset` 파일로 둘 다 다시 측정 |
| 시트의 "원문 열기"가 안 열림 | `--doc-base`가 웹 UI 주소(포트 8572)인지, 해당 source_type 문서를 `doc.html`이 여는지 확인. 링크 없이도 제목·external_id로 문서를 찾을 수 있다 |
