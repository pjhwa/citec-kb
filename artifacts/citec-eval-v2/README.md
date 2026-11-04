# 평가셋 v2 (artifacts/citec-eval-v2)

**상태: 아직 생성되지 않았다.** 운영 로그 추출과 사람 검수(O/△/X)가 끝나면 아래 파일이 이곳에 추가된다.

- 목적: 실사용 질의 + 합성 질의(`vocab`/`pointer`/`symptom`)를 풀링·LLM 보조·사람 검수로 판정한 60~80 질의 평가셋. 군 이름 `v2_real`, `v2_vocab`, `v2_pointer`, `v2_symptom`.
- 파일: `evalset-v2-all.json`(46건, 개선 전·후 비교에 쓰는 단일 평가셋), `v2all-baseline.json`(2026-11-03 기준선), `report.json`, `review-labels.json`(사람 검수 원본), `judged.json`.
- 분할: 도구는 dev/frozen을 만들지만, 첫 평가셋은 분할이 군별로 치우쳐(vocab 15/4, pointer 1/7) 한 벌로 합쳤다. 동결 홀드아웃은 없다. 케이스의 `split` 필드는 원래 분할(출처)이다. 새 frozen이 필요하면 추가 질의를 검수해 만든다. symptom 군은 미검수라 없다. `v2_real`은 recall@10=1.0으로 포화돼 악화 감시용이다.
- 판정 의미: `expect`는 O, `also_ok`는 △(게이트는 무시). v2 군은 recall@10 차이의 95% 구간이 전부 음수일 때만 게이트 실패이고, before/after의 케이스 id 집합이 다르면 종료 코드 1이다. 공통 케이스 8건 미만 군은 안내만 한다.
- 한계: 풀링으로 절대 recall이 부풀려질 수 있다(전/후 상대 비교만 유효). 합성 질의는 자기참조 편향이 남는다. 표시하지 않은 후보는 비관련으로 취급되고 △만 있는 질의는 제외된다. 군당 n이 작아 구간이 넓다. LLM 판정은 제안일 뿐이다.
- 문서 확장 실험(2026-11-04): `v2all-{off,grounded,all}.json`. recall@10 변화 없음(pointer 0.125, vocab 0.368, real 1.00). 자세한 내용은 `docs/DOC_EXPANSION_RUNBOOK_20261103.md` §9.
- 만드는 법·측정 명령: `docs/EVALSET_V2_RUNBOOK_20261102.md`
- 설계: `docs/superpowers/specs/2026-11-02-evalset-v2-design.md`
