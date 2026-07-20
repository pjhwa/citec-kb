# Phase 2+ 파일럿 체크리스트 (G2/G3 사인오프)

| 항목 | 내용 |
|------|------|
| 목적 | Fast QA Trust + 유사장애 + Phase3 planner 엔지니어링 완료 후 **도메인 워크스루** |
| 대상 | 지원 엔지니어 5–10명 · 그룹장 1명 |
| 환경 | `http://localhost:8572` · API `8573` |
| 일자 | (작성) |
| 계획 문서 | `IMPLEMENTATION_PLAN.md` v1.15+ |

## 사전 상태 (엔지니어링 — 자동화 가능)

- [x] Hybrid 검색 hit@3 ≥ 0.90
- [x] Fast/Deep QA + Trust 배너 + citation + multi_query
- [x] SSE stream
- [x] Issue frames 전량 적재
- [x] SI API/UI + gold eval
- [x] War-room 번들 + **쓰기 API**
- [x] Planner (list/analytics/capacity/prevention/exhaustive)
- [x] Entity/capacity/lexicon DB 시드
- [x] Worker job queue + audit log

### 자동 기술 점검 (사인 전 필수)

```bash
cd ~/dev/citec-kb
# 운영 readiness
curl -s localhost:8573/v1/ops/status | jq '{status,pilot_engineering_ready,worker:.checks.worker,seeds:.checks.seeds}'
# 시나리오 자동 스모크
.venv/bin/python scripts/pilot_tech_check.py
# 회귀
export PYTHONPATH=apps/api DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:8574/citec_knowledge
.venv/bin/python -m pytest apps/api/tests -q
.venv/bin/python -m app.eval.si_eval --gold data/gold/si_g01_g10.json
.venv/bin/python scripts/load_smoke.py --n 24 --concurrency 6
```

- [ ] `pilot_tech_check.py` exit 0
- [ ] `ops/status` pilot_engineering_ready=true

## 시나리오 워크스루 (사람)

### A. 검색
| # | 질의 | 기대 | 결과 (O/X) | 메모 |
|---|------|------|------------|------|
| A1 | CITECTS-2502 | 해당 티켓 top1 | | |
| A2 | 리눅스 파일시스템 체크리스트 | PISA/FS 관련 | | |
| A3 | 모니모 Redis | 관련 지원이력 | | |
| A4 | 서비스 base M/M FAQ | QRB FAQ 상위 | | |

### B. Fast QA
| # | 질의 | 기대 | 결과 | 메모 |
|---|------|------|------|------|
| B1 | CITECTS-2502 원인과 조치 | [C#] 인용 + 원인/조치 | | |
| B2 | 존재하지 않는 이슈 | 기권/약한 근거 | | |
| B3 | deep 모드 | 더 긴 구조화 답 | | |

### C. 유사장애 · 번들
| # | 증상 | 기대 | 결과 | 메모 |
|---|------|------|------|------|
| C1 | 모니모 Redis 타임아웃 | 유사 사례 + 적용성 | | |
| C2 | soft lockup hang | linux-hang 번들 힌트 | | |
| C3 | Oracle tablespace 부족 | Redis 사례 적용성=가능 금지 | | |

### D. Phase 3 관리자 질의
| # | 질의 | 기대 | 결과 | 메모 |
|---|------|------|------|------|
| D1 | 지난 주 지원건 | 목록 total≥0 | | |
| D2 | 연도별 지원 건수 | analytics buckets | | |
| D3 | 2주 Linux 대수 | capacity units=40 | | |
| D4 | 장애지원 제목 패턴 | title_tokens | | |

## 합격 기준 (파일럿)
- [ ] A–D 시나리오 중 ≥ 80% O
- [ ] 기권 시 단정 조치 문구 0건
- [ ] 그룹장 1회 워크스루 코멘트 기록
- [ ] 치명 이슈 0건

## 사인
| 역할 | 이름 | 일자 | 서명 |
|------|------|------|------|
| BE | | | |
| 도메인 시니어 | | | |
| 그룹장 | | | |
