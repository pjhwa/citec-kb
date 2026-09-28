# confluence_map 커버리지 갭 (2026-09-28)

## 사건

`kb_search(section=confluence_map)`로 "OpenStack Neutron L3 에이전트 장애"를 찾으면 점수가 낮은
부분 일치뿐이었고, 처음에는 "코퍼스 공백"으로 분류됐다. 실제로는 Confluence 전체에 관련 페이지가
1,141건 있었고, 그중 Neutron 트러블슈팅 문서 두 건(Openstack101 `1857584042`, DevOps001
`2125218057`)은 이미 등록된 스페이스 안에 있었다. 원인은 이 페이지들이 각 스페이스에 등록된
`roots`/`explicit_pages` 어느 것의 하위도 아니라서 **크롤 범위 밖**이었던 것이다 (DevOps001 쪽은 조직
개편 후 생긴 "B-18. 셀별 공간 > SRE파트 > 2.N셀" 트리 아래).

## 조치

| 항목 | 내용 |
|------|------|
| root 추가 | Openstack101 `1816390936`, DevOps001 `2125217911` (둘 다 "운영매뉴얼/트러블슈팅") — `map_source_seed.py` + 마이그레이션 `20260922_0008` (기존 DB의 `Source.config.roots`에 병합) |
| 관리 도구 | `POST /v1/confluence-map/sources/{source_id}/roots` `{page_id, label}` — 기존 source의 roots에 병합(멱등, 다른 root·checkpoint 보존). `POST /v1/confluence-map/sources`의 "새 스페이스만"(409) 계약은 그대로 |
| 재감사 | `scripts/audit_map_root_coverage.py` (운영 환경 전용, 읽기 전용) |

**배포와 재크롤은 별개 단계다.** 마이그레이션은 설정만 바꾼다. 새 root 아래 페이지가 색인되려면 배포 후
두 소스만 따로 돌려야 한다 (예: 관리자 트리거 `run_map_inventory` / `scripts/map_sync.sh --source-ids
confluence_map_openstack101,confluence_map_devops001`, 먼저 `--dry-run`).

root를 "운영매뉴얼" 한 단계 위로 잡지 않고 트러블슈팅 폴더 자체로 좁게 잡았다 — 상위에는 설치/구성
가이드도 있어 유사장애 검색에 노이즈만 늘린다.

## 커버리지 갭 진단 순서

특정 주제 결과가 약할 때 "코퍼스에 없다"로 단정하지 말고 이 순서로 본다.

1. **광역 확인** — confluence-mcp `searchContent`로 Confluence 전체 CQL
   (`text ~ "<핵심어>" OR title ~ "<핵심어>"`). `totalSize`가 크면 콘텐츠는 있다.
2. **위치 확인** — 히트의 `space_key`가 등록 스페이스인지.
3. **커버리지 확인** — `getPageByID(expand=ancestors)`의 조상 id 체인에 그 스페이스의 `roots`/
   `explicit_pages` id가 하나라도 있는지. 없으면 커버리지 갭(설정으로 고침).
4. 커버리지가 맞을 때만 랭킹/임베딩(용어 불일치, 벡터 유사도) 문제로 좁힌다.

## 재감사 스크립트

```
python3 scripts/audit_map_root_coverage.py                       # 부분 크롤 스페이스 전체, depth 1
python3 scripts/audit_map_root_coverage.py --source-ids confluence_map_cldeng --depth 3
```

스페이스 홈에서 `--depth`단계까지 내려가며 제목이 `이슈|장애|트러블슈팅|SOP|KDB|케이스 스터디`에 맞고
등록된 root/explicit page가 아닌 폴더를 후보로 출력한다 (등록된 root 아래로는 내려가지 않는다).
**후보는 사람이 검토해서 추가한다 — 자동 추가 금지** (개인 워크스페이스가 딸려 들어온다).

`--depth` 기본값은 1이지만, 이번 갭은 스페이스 홈에서 5단계 아래에 있었다. depth 1만으로는 같은 종류의
갭을 놓치므로 실제 감사에서는 `--depth`를 올려서 돌린다 (단계마다 폴더 수만큼 요청이 든다).

## 남긴 결정

- 두 스페이스를 전체 스페이스 크롤로 바꾸는 안은 채택하지 않았다. 시드 주석이 의도적으로 얕게
  잡은 이유("thinner")를 남겨 뒀고, 비용 대비 효과는 사람이 다시 판단할 사항이다.
- 상시 자동 탐지(주기 배치)는 필수가 아니어서 만들지 않았다.
