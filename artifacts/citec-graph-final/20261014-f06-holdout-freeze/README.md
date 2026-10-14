# F06(R05) 홀드아웃 동결 — 2026-10-14

제안: `docs/superpowers/specs/2026-10-14-f06-holdout-proposal.md` (방법론, 사용자 승인됨: N=30 + 도메인 배분).
초안: `docs/superpowers/specs/2026-10-14-f06-holdout-draft.json` (fork 에이전트가 실제 MCP 호출로 작성).
동결: `evaluation/holdout-2026-10-14.json` + `evaluation/holdout-2026-10-14.sha256`.

이 동결 이후 `evaluation/holdout-2026-10-14.json`은 읽고만 쓴다 — 랭킹 수정 실패 후 기준을 몰래 완화하려고 이 파일을 고치지 않는다(핸드오프 §3.3). 표본을 바꿔야 하면 새 파일+새 hash로, 왜 바꿨는지 기록하고 사용자 승인을 다시 받는다.

**baseline 측정 완료**: `evaluation/baseline-cost-2026-10-14.json` — 현재 구현(kb_search만, 그래프 미사용)으로 30문항 전부 실제 호출. Recall@10=43.3%(13/30), MRR=0.141, p50=10.9s/p95=15.5s(측정 방식의 한계로 순수 서버 p95이 아님, 파일 내 `methodology_caveat` 참고). 도메인별로는 Linux 100%(1/1)부터 OpenStack/Kubernetes/Windows/Ceph 0%까지 편차가 큼(표본 수가 1~2건이라 통계적 의미는 제한적). 질적으로 중요한 발견 2건: (1) network-06은 질문 문구에 따라 같은 정답 문서가 안 잡힘(F06/R05가 풀어야 할 "질문 문맥" 격차의 실제 사례), (2) storage-02/kubernetes-01은 "정답이 다른 doc_id/source_type으로 존재"하는 케이스 — F05의 namespace/identity 논의와도 맞닿아 있음.

**다음 단계**: 이 baseline 수치를 사용자가 검토한 뒤, (a) 비용 목표를 정하고 (b) 비교군 B(kb_search+그래프)까지 측정해 baseline 대비 개선 여부를 판단하는 순서로 진행 — 제안서가 요구한 "baseline 먼저, 그 다음 목표/수정" 순서를 지킨다. 랭킹 코드는 아직 전혀 건드리지 않았다.

## 동결 내용 요약

- 30문항, 11개 도메인(Network 11, 성능 4, Middleware 3, Database 2, VMware 2, Storage 2, OpenStack 2, Kubernetes 1, Windows 1, Linux 1, Ceph 1) — 승인된 비례 배분 그대로.
- 핸드오프의 "알려진 회귀" 전부 제외 확인: TGW 문서 3건, NetApp 회귀 문서(`tech_repo:1932298903`), CITECTS-2502, 기존 8개 failure_bucket, 평가 제외 root(`2550166358`)와 후손 7건, 기존 48개 검색 질의 12개 고유 문자열.
- 사용자가 직접 결정한 지점 2건: `storage-02`(MCC/SnapMirror 정확 사례가 dev 데이터에 없어 NetApp iSCSI 암호화 동기화 유사 사례로 대체, 그대로 동결), `network-04`(후보 매치가 약함, 그대로 동결).
- `confirmation_provenance`(동결 파일 안에 그대로 기록): 방법론·표본 배분은 사용자 승인, 명시적으로 짚은 2건의 edge case도 사용자가 직접 결정. 나머지 28건의 정답 문서는 AI가 kb_search/kb_similar_incident로 찾은 것이며, 사람이 문서 하나하나를 원문으로 재확인하지는 않았다 — 핸드오프의 "정답은 원문으로 판정" 요구를 방법론 수준에서는 충족하지만, 문항 단위 전수 검증은 아니다. 나중에 전수 검증이 필요하면 이 파일을 제자리에서 고치면 된다(표본을 다시 뽑을 필요 없음).
