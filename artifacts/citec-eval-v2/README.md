# 평가셋 v2 (artifacts/citec-eval-v2)

**상태: 아직 생성되지 않았다.** 운영 로그 추출과 사람 검수(O/△/X)가 끝나면 아래 파일이 이곳에 추가된다.

- 목적: 실사용 질의 + 합성 질의(`vocab`/`pointer`/`symptom`)를 풀링·LLM 보조·사람 검수로 판정한 60~80 질의 평가셋. 군 이름 `v2_real`, `v2_vocab`, `v2_pointer`, `v2_symptom`.
- 예정 파일: `evalset-v2-dev.json`(튜닝·개선 확인용), `evalset-v2-frozen.json`(동결, 릴리스 판정 전용), `report.json`(kept/no_answer/unreviewed), `review-labels.json`(사람 검수 원본), `judged.json`.
- 분할: 질의 문구 해시로 dev/frozen이 결정되며(대략 반반) frozen에는 `frozen_at_utc`가 기록된다. **frozen을 보고 튜닝하지 않는다.**
- 판정 의미: `expect`는 O, `also_ok`는 △(게이트는 무시). v2 군은 recall@10 차이의 95% 구간이 전부 음수일 때만 게이트 실패이고, before/after는 같은 평가셋이어야 한다.
- 한계: 풀링으로 절대 recall이 부풀려질 수 있다(전/후 상대 비교만 유효). 합성 질의는 자기참조 편향이 남는다. 군당 n이 작아 구간이 넓다. LLM 판정은 제안일 뿐이다.
- 만드는 법·측정 명령: `docs/EVALSET_V2_RUNBOOK_20261102.md`
- 설계: `docs/superpowers/specs/2026-11-02-evalset-v2-design.md`
