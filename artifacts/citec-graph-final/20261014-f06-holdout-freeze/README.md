# F06(R05) 홀드아웃 동결 — 2026-10-14

제안: `docs/superpowers/specs/2026-10-14-f06-holdout-proposal.md` (방법론, 사용자 승인됨: N=30 + 도메인 배분).
초안: `docs/superpowers/specs/2026-10-14-f06-holdout-draft.json` (fork 에이전트가 실제 MCP 호출로 작성).
동결: `evaluation/holdout-2026-10-14.json` + `evaluation/holdout-2026-10-14.sha256`.

이 동결 이후 `evaluation/holdout-2026-10-14.json`은 읽고만 쓴다 — 랭킹 수정 실패 후 기준을 몰래 완화하려고 이 파일을 고치지 않는다(핸드오프 §3.3). 표본을 바꿔야 하면 새 파일+새 hash로, 왜 바꿨는지 기록하고 사용자 승인을 다시 받는다.

**아직 하지 않은 것(다음 단계)**: 현재 구현으로 이 30문항을 돌려 baseline 비용(툴 호출 수, p50/p95, DB roundtrip)을 측정하고, 그 실측값을 근거로 비용 목표를 정하는 일 — 제안서 §5가 요구한 순서대로, 랭킹 코드를 건드리기 전에 먼저 해야 한다.

## 동결 내용 요약

- 30문항, 11개 도메인(Network 11, 성능 4, Middleware 3, Database 2, VMware 2, Storage 2, OpenStack 2, Kubernetes 1, Windows 1, Linux 1, Ceph 1) — 승인된 비례 배분 그대로.
- 핸드오프의 "알려진 회귀" 전부 제외 확인: TGW 문서 3건, NetApp 회귀 문서(`tech_repo:1932298903`), CITECTS-2502, 기존 8개 failure_bucket, 평가 제외 root(`2550166358`)와 후손 7건, 기존 48개 검색 질의 12개 고유 문자열.
- 사용자가 직접 결정한 지점 2건: `storage-02`(MCC/SnapMirror 정확 사례가 dev 데이터에 없어 NetApp iSCSI 암호화 동기화 유사 사례로 대체, 그대로 동결), `network-04`(후보 매치가 약함, 그대로 동결).
- `confirmation_provenance`(동결 파일 안에 그대로 기록): 방법론·표본 배분은 사용자 승인, 명시적으로 짚은 2건의 edge case도 사용자가 직접 결정. 나머지 28건의 정답 문서는 AI가 kb_search/kb_similar_incident로 찾은 것이며, 사람이 문서 하나하나를 원문으로 재확인하지는 않았다 — 핸드오프의 "정답은 원문으로 판정" 요구를 방법론 수준에서는 충족하지만, 문항 단위 전수 검증은 아니다. 나중에 전수 검증이 필요하면 이 파일을 제자리에서 고치면 된다(표본을 다시 뽑을 필요 없음).
