# scripts/verify/ — 지식그래프 G01~G05 실패 주입·재현 스크립트

2026-10-12 핸드오프(`docs/features/graph-evidence.md`) §3.1 요구: 순수 함수
확인/격리 DB 통합/REST/실제 MCP 호출을 구분해 원시 결과를 남긴다.

모든 스크립트는 **전용 scratch Neo4j + scratch Postgres**에서만 실행한다 —
dev/운영 공유 인스턴스에는 절대 쓰지 않는다. 실행 전 아래 두 환경변수가
가리키는 대상이 scratch인지 사람이 직접 확인한다:

```bash
export GRAPH_TEST_DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:8574/citec_graph_test"
export GRAPH_NEO4J_TEST_URI="bolt://localhost:17687"
export GRAPH_NEO4J_TEST_USER="neo4j"
export GRAPH_NEO4J_TEST_PASSWORD="<SCRATCH_NEO4J_PASSWORD>"
```

| 스크립트 | 확인 항목 | 출력 |
|---|---|---|
| `generation_mint_and_skip.sh` | G05 — 마커 없을 때 새 generation 발급, 마커 삭제 후 재발급 시 전체 재동기화, 같은 마커면 전부 skip | `/tmp/graph-verify-generation.log` |
| `hub_recompute_failure_injection.py` | G05 — hub 재집계 실패 시 `completed_warn`/exit 1 | stdout + exit code |
| `crash_mid_run_injection.py` | G05 — 루프 중 예외 시 `graph_runs`가 `running`에 멈추지 않고 `failed`로 전환 | stdout + exit code |

커밋되는 로그/출력에는 호스트명·비밀번호 등 민감정보를 남기지 않는다(스크립트가
scratch 접속 정보만 출력).
