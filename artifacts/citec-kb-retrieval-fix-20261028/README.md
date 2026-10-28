# 검색·도구 개선 평가 자산 (2026-10-28)

- `evalset-2026-10-28.json` — 추가 평가셋(체크리스트·증상 문장·식별자·불변식·스킬 스모크·B2 probe). 정답은 dev DB 원문을 열어 확인했고 사람 재확인 전이다. 증상 문장 질문은 정답 문서에서 파생돼 쉬운 편이라 절대 수치보다 전/후 차이로 본다.
- `baseline-A.json` — **A 단계(A1~A7) 반영 직후, B 변경 전** dev 측정(`git_sha` 필드 참고). `b2_probe_unfiltered` 군은 홀드아웃·나머지 군과 별도 시각에 측정해 병합했다(`merged_groups`).
- 하네스: `scripts/verify/eval_retrieval_gate.py` (`run` / `compare`, 읽기 전용, REST만 호출).
- 동결 홀드아웃: `artifacts/citec-graph-final/20261014-f06-holdout-freeze/evaluation/holdout-2026-10-14.json`

## 재측정
```
NO_COLOR=1 python3 -u scripts/verify/eval_retrieval_gate.py run --base-url http://localhost:8573 \
  --holdout artifacts/citec-graph-final/20261014-f06-holdout-freeze/evaluation/holdout-2026-10-14.json \
  --evalset artifacts/citec-kb-retrieval-fix-20261028/evalset-2026-10-28.json \
  --label after-B1 --out artifacts/citec-kb-retrieval-fix-20261028/after-B1.json     # 약 18분(dev 지연 때문)
python3 scripts/verify/eval_retrieval_gate.py compare artifacts/citec-kb-retrieval-fix-20261028/baseline-A.json \
  artifacts/citec-kb-retrieval-fix-20261028/after-B1.json                                # 종료코드 1 = 머지 금지
# 일부 군만: --only-groups checklist_oom,checklist_inode  (기존 --out 파일에 병합)
```
측정 전 dev api를 새 코드로 재시작해야 한다(`docker compose restart api`; 소스는 bind mount지만 프로세스는 자동 재로드되지 않는다).

## baseline-A 요약 (dev, 코퍼스 규모는 운영과 비슷하나 데이터·지연은 다름)
| 항목 | 값 |
|---|---|
| 홀드아웃 recall@5 / @10 / MRR (multi_query on, MCP 기본) | 46.7% / 53.3% / 0.310 |
| 홀드아웃 recall@5 / @10 / MRR (multi_query off) | 30.0% / 43.3% / 0.139 |
| 홀드아웃 상위 5 pointer-only 비율 | 1.3% (운영 에이전트 측정 60%와 크게 다름 — 홀드아웃은 장애 질의라 map이 적게 올라옴) |
| b2_probe(일반 키워드 8건) 상위 5 pointer-only 비율 | 18.1% (Redis timeout 80%, 나머지 0~25%) |
| 지연 p50/p95 (on) | 16.7 s / 19.7 s — dev CPU 임베딩 때문, **전/후 상대 비교에만 사용** |
| 군별 통과 | checklist_oom 2/6, checklist_inode 0/2, checklist_memleak 2/2, symptom(필터 없음) 4/7, symptom(support_history) 4/7, similar_incident 5/7, identifier 5/5, invariants 3/4, skill_smoke 2/2 |

### 재현된 약점 (이 기준선에서 실패하는 케이스)
- B1: `Linux OOM`(순위 없음), `OOM`(6위), `Linux Out of Memory`(9위), 필터 없는 `Linux OOM 점검항목`(순위 없음). `Out of Memory 로그 dmesg`·`메모리 과다 사용 이력`은 1위(본문과 맞으면 잡힘).
- inode: `PISAOLNX_04.04.03`이 8위(상위는 `PISAOLNX_01.01.01` 등). 컨테이너 항목 `PISAVSOS_01.02.08`은 상위 8 안에 없었다 → 프롬프트의 "컨테이너 항목이 위" 서술은 dev에서 재현되지 않고, 문제는 정답이 8위로 밀리는 것.
- B3: CITECTS-2502 증상 문장 2종이 필터 없는 검색에서 상위 10 밖, `support_history` 필터에서 3위. `kb_similar_incident`는 둘 다 1위.
- B4: `2026년 9월 기술지원 목록` → `hybrid_search` 분기(기대: `time_scoped_list`, `/v1/tickets` 38건과 일치).
- 별개 약점: CITECTS-2634(NAS/eNAS 지연)는 검색 3종 모두 상위권 밖, CITECTS-2508은 support_history 10위·similar_incident 4위.

### 통과(A 단계 효과 확인)
- identifier 5/5 — 없는 번호는 `missing_identifiers`+weak/empty, 있는 번호는 변화 없음(A6).
- invariants — `entity_share` `matched<=total` 및 `kb_analytics(entity=)`와 일치(SCP 137/278, redis 2/278), `작년 SCP 건수`=175로 `kb_analytics`와 일치(A2).
