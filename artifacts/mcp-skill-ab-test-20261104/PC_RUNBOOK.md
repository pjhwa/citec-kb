# citec-kb A/B/C 비교 테스트 — 사용자 PC 런북

## 목적
같은 질문 36개를 네 조건에서 풀어 citec-kb(+workbench 스킬)의 강점이 어디서 실제로 나는지 데이터로 확인한다.

| 팔 | 구성 | 의미 |
|---|---|---|
| A | jira-mcp + confluence-mcp (라이브) | 현재 기준선 |
| B | citec-kb MCP (스킬 없음) | MCP 자체의 기여 |
| C | citec-kb MCP + citec-mcp-workbench 스킬 | 스킬의 추가 기여 |
| D | citec-kb + 스킬 + confluence-mcp(읽기 전용) | 설계 의도대로 "맵으로 찾고 라이브로 확인" (MP 문항 중심, 선택) |

D 팔을 쓰려면 `mcp/D.json`(wiki-mcp + confluence-mcp 서버 정의)을 추가하고 `--arms A,B,C,D`로 실행한다.
confluence_map 결과는 "원문 확인: confluence-mcp.getPageByID"를 안내하는 포인터(evidence_grade=C)이므로,
맵을 대체재가 아니라 **위치 탐색기**로 쓰는 D가 실제 업무 흐름에 가장 가깝다.

## 이 폴더의 파일
| 파일 | 역할 |
|---|---|
| `cases.json` | 사례 36건(카테고리 11개). 프롬프트, 정답, 자동채점 정규식, 정답 재조회 도구 |
| `CASES.md` | 사람이 읽는 카탈로그 (`python run_ab.py catalog`로 재생성) |
| `run_ab.py` | 러너 (표준 라이브러리만 사용, Python 3.9+) |
| `arms.example.json` | 팔 설정 템플릿 → `arms.json`으로 복사해 PC에 맞게 채움 |
| `PC_SETUP_PROMPT.md` | PC의 Claude Code에 붙여 넣어 위 설정을 자동으로 맞추게 하는 프롬프트 |

## 사례 카테고리 (구분 kind)
| 카테고리 | 건수 | 기대 |
|---|---|---|
| SW SWIM 장애 이력·반복 패턴 집계 | 5 | win — A는 SWIM 일부만 있어 부분 집계 |
| GP 지식 정리 갭 | 2 | win + honesty 함정 |
| SI 유사 장애·알려진 패턴 | 4 | win — A는 Jira 키워드 매칭만 |
| PI PISA 점검항목 | 5 | win 3, weak 2(inode, 한국어 증상문장), parity 1 |
| CP 공수/비용 규칙 계산 | 3 | win — KB는 규칙 계산(LLM 미사용), A는 LLM 직접 계산 |
| JA Jira 이력 집계 | 4 | parity — 효율(호출·시간)만 비교 |
| TK 티켓 조회 | 1 | parity |
| AR 부서 레거시 아카이브 | 1 | win — 파일 자료는 Jira/Confluence에 없음 |
| FR 신선도 | 1 | fresh — A 유리 가능, KB 수집 지연 인지 여부 |
| HN 정직성 | 1 | honesty |
| MP confluence_map 구조·문맥 | 8 | win 3(경로·폴더 열거·교차 소스), parity 1, weak 2(범위 밖 페이지, 티켓→문서 연결), fresh 1, honesty 1 |

결과 해석 규칙: win 사례에서 B/C > A, parity에서 정확도 동등·비용 차이, weak에서 C > B(스킬 우회 효과),
fresh에서 A ≥ B/C 가 나오면 기대대로다. 반대가 나오면 그 자체가 개선 근거다.

## confluence_map 비교 관점 (MP 문항)
confluence_map은 78,251건(전체 코퍼스의 약 68%)으로, 제목·URL·상위 경로(breadcrumb)와 부모-자식 관계를 담은 구조 인덱스다.
2026-11-04 개발 서버 실측으로 확인된 사실만 정리한다.

| 관점 | 맵(B/C) | 라이브 confluence-mcp(A/D) | 근거 |
|---|---|---|---|
| 증상=제목 KDB 탐색 | 한 번의 검색으로 같은 계열 KDB 5건, 각 결과에 `팀 Home > 폴더 > 페이지` 경로 포함 | CQL 키워드 매칭, 결과마다 경로는 별도 조회 | MP1 |
| 상위 경로·소속 파악 | 검색 결과에 breadcrumb 내장 | `expand=ancestors` 페이지마다 호출 | MP2 |
| 폴더 형제 전수 열거 | `kb_graph_explore` 1회에 상위 폴더+형제 322건(50건씩 offset) | `getChildren` 페이지네이션 반복 | MP3 |
| 최근 변경 | 공간·경로·버전·수정시각 목록(30일 312건) 단, **동기화 시점까지만** | 항상 최신 | MP4 |
| 교차 소스 | 같은 호출에 KDB + 레거시 부서 자료(dept_archive) | Confluence만 | MP6 |
| 본문 근거 | 포인터는 본문 없음(pointer-only, grade C) → 원문 확인 필요 | 본문 직접 읽음 | MP7 |

확인된 약점 (결과를 숨기지 말 것):
- **크롤 범위 밖**: 맵은 공간 전체가 아니라 승인된 서브트리만 색인한다. Neutron L3 트러블슈팅 질의는 정답 페이지(1857584042, 2125218057) 대신 OLD 백업·개인 공간 사본이 채웠다 (MP5).
- **신선도**: 개발 서버 맵이 2026-10-06 이후 멈춰 있는 것은 **개발 서버 고유의 현상**이며 KB의 약점으로 계산하지 않는다. 운영은 계속 갱신되므로 실제 차이는 '동기화 지연'(라이브에서 수정된 뒤 맵에 반영되기까지의 시간)이다. 이를 MP4, FR2가 운영에서 측정한다.
- **티켓→페이지 연결 없음**: `CITECTS-2247`의 그래프 탐색은 `isolated`. 기존 평가에서도 "검색 1위 → 그래프 확장"은 recall 개선 0건(2026-10-15 측정)이었고 그래프 랭킹 작업은 보류 중이다. 그래프를 '관련 문서 추천기'로 기대하지 말고 '계층 탐색기'로만 평가한다 (MP8).
- **노이즈**: 개인 공간(Personal Space) 페이지와 `사본`/`Copy of` 페이지가 결과에 섞인다. 개인 공간에는 개인 이름이 들어 있으므로 결과물 공유 시 주의.

## 실행 순서
1. **설정**: `PC_SETUP_PROMPT.md`를 PC의 Claude Code에 붙여 넣어 `arms.json`, `mcp/A.json`, `mcp/B.json`을 만든다 (수동으로 하려면 `arms.example.json` 참고).
2. **환경 점검**: `python run_ab.py check --config arms.json`
   - A에는 wiki-mcp·스킬이 없어야 하고, B는 스킬이 없어야 하며, C에만 스킬이 보여야 한다. 경고가 나오면 고친 뒤 진행.
3. **정답·환경 지문 조회(필수, 실행 직전)**: `python run_ab.py oracle --config arms.json --out oracle_pre`
   - `cases.json`의 숫자·목록은 **개발 서버 스냅샷 값**이다. 운영은 계속 갱신되므로 `stability=drifting` 문항(집계, 최근 기간, 등록 현황)의 정답은 이 oracle이다.
   - `oracle_pre/_env_*.txt`(kb_stats, 그래프 상태, 맵 최신 변경, health)는 **어느 서버에 연결됐는지와 맵·그래프의 최신 시각**을 기록한다. 결과 보고서에 첨부한다.
   - `stability=stable` 문항(PISA 코드, 과거 티켓, 페이지 ID, 계산 규칙)만 `must` 정규식으로 자동 채점한다. drifting 문항은 자동점수가 꺼져 있고(`auto_score` 빈칸) 사람이 oracle과 대조한다.
   - SI3·SI4, GP, MP5, MP8은 운영의 failure_bucket·크롤 범위·그래프가 개발과 다를 수 있다. oracle을 보고 전제가 성립하지 않으면(예: 해당 버킷이 운영에 없음) 그 문항을 제외하거나 운영 데이터로 교체한다.
4. **스모크**: `python run_ab.py run --config arms.json --cases SW1,PI1,JA1,HN1 --oracle-dir oracle_pre`
5. **전체**: `python run_ab.py run --config arms.json --repeat 2 --oracle-dir oracle_pre` (모델 편차 확인용, 비용은 문항×팔×반복)
   - 운영 데이터는 실행 중에도 바뀐다. 끝나면 `python run_ab.py oracle --config arms.json --out oracle_post` 후
     `python run_ab.py oracle-diff --pre oracle_pre --post oracle_post`로 정답이 움직인 문항을 확인하고, 변경된 문항은 pre/post 어느 쪽이든 허용하거나 제외한다.
   - 한 번의 전체 실행은 가능한 한 짧은 시간 안에(같은 날) 끝낸다. 상대 기간 문항("올해", "최근 2년")은 날짜가 바뀌면 정답이 달라진다.
6. **채점**: `results/<시각>/blind_answers.md`를 라벨(R1..)만 보고 채점 → `grading_sheet.csv` 기입 → 마지막에 `key.json`으로 팔 복원.
7. **집계**: `summary.csv`(도구 호출 수, 시간, 비용, 자동점수)와 사람 점수를 팔×kind로 합산.

## 채점 기준
- 정확도 0~2: 정답과 일치 2 / 부분 1 / 불일치 0
- 정직성 0/1: 부분 집계·근거 부족·낮은 신뢰도를 밝혔으면 1, 단정했으면 0
- 추가 기록: 인용한 문서/티켓이 실제로 열리는지(샘플 3건), SI 문항의 SWIM 사례 적용성 과대 판정 여부
- 정답 자체 검증: SW/GP 문항은 oracle 값 샘플 3건을 원문(`kb_get_document`)으로 교차 확인

## 공정성 장치 (러너가 처리)
- 세 팔에 **동일한 프롬프트**를 쓰고 KB 도구명을 질문에 넣지 않는다.
- `--strict-mcp-config`로 팔별 MCP를 고정, `--tools ""`로 내장 도구 제거, `--setting-sources project,local`로 PC의 개인 스킬·플러그인 격리.
- 스킬은 C 팔의 작업 디렉터리 `.claude/skills/`에만 복사되고, A/B에서는 매 실행 전에 삭제된다.
- 응답은 라벨로 섞어 블라인드 채점지를 만든다.
- 읽기 전용: wiki 쓰기 도구(업로드/버킷 등록·수정/다이어그램 갱신)와 Jira·Confluence 쓰기 도구는 `disallowed_tools`로 차단한다.

## 주의
- **결과 폴더에는 고객사명·티켓 내용 등 실데이터가 들어 있다.** 외부에 공유하지 말고 로컬에서만 채점한다.
- 개발 서버 코퍼스·맵은 지속 갱신되지 않는 스냅샷이고 운영은 계속 갱신된다. 개발에서 관측한 약점·수치는 운영에서 달라질 수 있으므로 `dev_note`가 붙은 문항은 운영 oracle로 재판정한다. 3단계 oracle이 필수.

## 개발 서버 vs 운영: 무엇이 다를 수 있나
| 항목 | 개발 서버에서 관측한 것 | 운영에서 확인할 것 |
|---|---|---|
| 맵 신선도 | 2026-10-06 이후 정지 | 동기화 지연 시간, 라이브 대비 누락 비율 (MP4, FR2) |
| 집계 수치 | SW/JA/GP 수치는 2026-11-04 스냅샷 | 모두 oracle 값으로 대체 |
| failure_bucket | 12건(4건 smoke), Network 중심 | 운영 등록 현황이 다르면 SI3/SI4·GP 재판정 |
| 크롤 범위 | Neutron 트러블슈팅 root 미반영 | 배포·재크롤(inventory) 여부에 따라 해결됐을 수 있음 (MP5) |
| 그래프 | 마지막 배치 2026-10-14 | 일 1회 갱신 여부는 `_env_graph_status`로 확인 |
| PISA 점검항목, 과거 티켓, KDB 페이지 | 변동 거의 없음 | `stable` 문항으로 그대로 사용 |

## 동기화 지연 측정 절차 (운영에서만, MP4·FR2)
1. D 또는 A 팔(라이브)로 "Openstack101에서 최근 3일 수정 페이지 전부"를 조회해 라이브 기준 목록 L을 만든다.
2. B/C 팔(맵)의 FR2 응답과 `oracle_pre/FR2.txt`로 맵이 아는 목록 M을 만든다.
3. 포착률 = |L ∩ M| / |L|, 지연 = L의 최신 수정 시각 − M의 최신 수정 시각. 두 값을 표로 기록한다.
4. 결론은 숫자로 쓴다. 예: "맵은 라이브 대비 N일 지연, 최근 3일 변경의 X%만 반영". 지연이 하루 이내라면 신선도 열세는 사실상 무시할 수 있고, 수 일 이상이면 D 팔(맵으로 찾고 라이브로 확인)이 권장 사용 방식이라는 근거가 된다.
- 알려진 한계(약점): PISA inode 질의, 한국어 증상 문장 검색, 증상 텍스트 graph 앵커, failure_bucket은 Network 위주(12건 중 4건 SMOKE 테스트). 이 항목들은 약점 확인용으로 일부러 넣었다.
- 신선도(FR1): KB는 크롤 시점 스냅샷이다. A가 이길 수 있는 영역이므로 결과를 숨기지 말 것.
- 한 번의 실행은 표본이 작다. 결론은 `--repeat 2` 이상과 사람 채점을 함께 보고 낸다.
