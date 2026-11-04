# PC 재구성 프롬프트 (PC의 Claude Code에 붙여 넣기)

아래 블록을 이 폴더(`mcp-skill-ab-test-20261104`)를 작업 디렉터리로 연 Claude Code에 그대로 붙여 넣는다.

~~~
이 폴더는 citec-kb MCP(+workbench 스킬)와 Jira/Confluence MCP를 비교하는 A/B/C 테스트 팩이다.
PC_RUNBOOK.md, cases.json, run_ab.py, arms.example.json 을 먼저 읽고, 이 PC 환경에 맞게 설정을 만들어 줘.
테스트 실행 자체는 내가 허락하기 전까지 하지 말고, 환경 점검(check)까지만 해.

해야 할 일:
1. 환경 파악
   - OS, `python --version`(3.9 이상 필요), `claude --version`, `claude mcp list` 결과를 확인한다.
   - 이 PC에 등록된 MCP 서버 중 Jira, Confluence, citec-kb(wiki-mcp 계열) 서버의 이름과 연결 방식을 파악한다.
   - 토큰·비밀번호·URL 중 인증정보는 화면에 출력하지 않는다. 설정 파일은 복사만 하고 내용을 인용하지 않는다.
2. MCP 설정 파일 생성
   - `mcp/A.json`: Jira, Confluence 서버 정의만 담는다 (기존 설정에서 해당 서버 블록만 복사).
   - `mcp/B.json`: citec-kb(wiki-mcp) 서버 정의만 담는다.
   - `mcp/D.json`: citec-kb(wiki-mcp) + Confluence 서버 정의만 담는다 (Jira는 제외).
   - citec-kb 서버의 이름이 `wiki-mcp`가 아니면, cases.json·arms.json의 `mcp__wiki-mcp` 접두사를 실제 서버 이름으로 일괄 치환한다.
3. 쓰기 도구 차단 목록
   - Jira, Confluence MCP가 노출하는 도구 이름을 조회해서, 이슈 생성/수정/삭제/전환/코멘트, 페이지 생성/수정/삭제 등
     모든 쓰기 도구를 `arms.json`의 A 팔 `disallowed_tools`에 정확한 이름(mcp__서버__도구)으로 넣는다.
   - 읽기 도구만 남는지 확인한다.
4. 스킬 경로
   - citec-mcp-workbench 스킬 폴더(SKILL.md가 들어 있는 폴더)를 이 PC에서 찾아 `arms.json`의 C, D 팔 `skill_src`에 넣는다.
   - D 팔의 Confluence 쓰기 도구 차단 목록도 A 팔과 동일하게 채운다 (읽기 도구만 허용).
   - 찾지 못하면 추측하지 말고 나에게 경로를 물어라.
5. `arms.example.json`을 복사해 `arms.json`을 완성한다. `<...>` 자리표시자가 하나도 남지 않아야 한다.
   - Windows이면 경로 구분자와 따옴표 처리를 확인하고, `claude` 실행 파일이 PATH에서 잡히는지 본다(`python -c "import shutil;print(shutil.which('claude'))"`).
6. `python run_ab.py check --config arms.json` 을 실행하고 결과를 보고한다.
   - 기대: A에는 wiki-mcp·스킬이 없음, B에는 스킬이 없음, C와 D에만 citec-mcp-workbench 스킬이 보임, D에는 Jira가 없음, 각 팔의 MCP가 connected.
   - `check`는 arms.json의 모든 팔을 점검한다. D 팔을 쓰지 않으려면 arms.json에서 D 블록을 지운다.
   - 경고가 있으면 원인을 고치고 다시 check 한다.
7. 마지막에 보고할 것
   - 만든/수정한 파일 목록, 각 팔의 MCP 서버·허용·차단 도구 요약, check 결과, 내가 결정해야 할 것.

하지 말 것:
- cases.json의 질문(prompt)을 바꾸지 마라. 세 팔은 같은 질문이어야 공정하다.
- citec-kb나 Jira/Confluence에 쓰기 작업을 하지 마라.
- 내가 허락하기 전에는 `run`이나 `oracle`을 실행하지 마라.
~~~

## 다음 단계 (check 통과 후, 사람이 진행)
1. `python run_ab.py oracle --config arms.json --out oracle_pre` — PC가 연결된 citec-kb(운영) 기준으로 정답과 환경 지문(서버 규모, 맵·그래프 최신 시각)을 받는다.
2. `oracle_pre/_env_*.txt`로 연결된 서버가 운영인지, 맵·그래프가 최근 갱신됐는지 확인한다.
3. `stability=drifting` 문항과 `dev_note`가 붙은 문항은 oracle을 정답으로 쓴다. 전제가 성립하지 않는 문항(운영에 해당 failure_bucket이 없는 경우 등)은 제외하거나 교체한다.
4. PC_RUNBOOK.md 4~7단계 진행 (`--oracle-dir oracle_pre`, 끝난 뒤 `oracle_post`와 `oracle-diff`).
