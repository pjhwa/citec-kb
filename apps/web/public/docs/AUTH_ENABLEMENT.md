# 인증 개시 절차서 (AUTH_MODE=apikey)

**현재 상태: `AUTH_MODE=off`** (코드 기본값과 `docker-compose.yml` 기본값 모두). 이 문서는 운영자가 켜는 시점에 따를 절차이며,
켜기 전까지 어떤 호출도 달라지지 않는다. 켜는 시점은 KB 담당자가 공지한다.

## 1. 켜기 전에 알아둘 동작

| 호출 | 인증 켜진 뒤 |
|---|---|
| 읽기: `GET /v1/failure-buckets`, `GET …/{id}`, `POST …/match`, 검색 | **토큰 없이 열려 있음** (현재 동작 그대로) |
| 쓰기: `POST /v1/failure-buckets`, `…/refine` | `author`·`senior`·`admin` |
| `POST …/{id}/retire`, `…/restore` | `senior`·`admin` |
| `POST …/retire-batch` | `admin` |

- 시스템 토큰(insight-ops, agent-room, ToolBox 도구)은 **`author` 롤 하나**만 받는다. 그래서 자기 등록건을 스스로 retire할 수 없다.
- `senior`/`admin`은 사람이 쓴다(`--allow-privileged`로만 발급).
- 토큰 이름은 `created_by`와 같은 값(`<시스템>@<판>`)으로 발급한다. 본문 `created_by`가 토큰 이름과 다르면 거부하지 않고 응답에 `created_by_warning`이 붙는다.
- 인증이 켜진 상태에서 토큰이 없거나 틀린 쓰기는 401, 롤이 모자라면 403이다.

## 2. 개시 순서

1. **토큰 발급** (운영 서버, 저장소 루트):
   ```bash
   python3 scripts/issue_system_token.py --name insight-ops@v1.4      # 토큰이 stdout에 한 번만 출력됨
   python3 scripts/issue_system_token.py --name agent-room@v0.9
   ```
   - `config/auth.json`에 `{토큰: {sub, name, roles:[author]}}`로 추가된다. 기존 항목은 보존된다. 쓰기는 임시 파일 후 rename이라 서버가 반쯤 쓰인 파일을 읽지 않는다.
   - 파일 권한이 느슨하면 스크립트가 경고한다. `chmod 600 config/auth.json`.
2. **각 시스템에 토큰 전달**: 보안 채널로. 받는 쪽은 환경 변수(예: `CITEC_KB_API_KEY`)로 보관하고 값이 있을 때만 `X-API-Key` 헤더를 붙인다.
3. **`AUTH_MODE=apikey` 설정**: `.env`의 `AUTH_MODE=apikey`.
4. **API 컨테이너 재시작**(재빌드 불필요): `docker compose up -d api`. `AUTH_MODE`는 프로세스 환경 변수라 재시작이 필요하다.
5. **확인** (아래 `$KB`는 API base URL):
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST $KB/v1/failure-buckets -H 'Content-Type: application/json' -d '{}'   # 401
   curl -s -o /dev/null -w '%{http_code}\n' $KB/v1/failure-buckets                                                   # 200 (읽기는 열려 있음)
   curl -s -X POST $KB/v1/failure-buckets/match -H 'Content-Type: application/json' -d '{"observed_signals":["x"]}'   # 200
   curl -s -o /dev/null -w '%{http_code}\n' -X POST $KB/v1/failure-buckets -H "X-API-Key: $TOKEN" \
        -H 'Content-Type: application/json' -d @body-test.json                                                          # 200 (created_by는 -test 접미로 시험)
   ```

## 3. 롤백

`.env`에서 `AUTH_MODE=off`로 되돌리고 `docker compose up -d api`. 모든 호출이 이전처럼 통과한다. 토큰 파일은 그대로 두어도 무해하다(off 모드는 토큰 표를 읽지 않는다).

## 4. 토큰 회수·교체 (재시작 불필요)

서버는 `config/auth.json`의 mtime/크기/inode가 바뀌면 **다음 요청부터** 새 표를 읽는다.

```bash
python3 scripts/issue_system_token.py --revoke insight-ops@v1.4           # 즉시 401
python3 scripts/issue_system_token.py --name insight-ops@v1.4 --rotate    # 기존 토큰 폐기 + 새 토큰
```

- 같은 이름이 이미 있으면 `--rotate` 없이는 발급하지 않는다.
- `config/auth.json`을 읽을 수 없거나 JSON이 깨지면 **파일의 모든 토큰이 거부**된다(회수한 토큰이 캐시에 남지 않게 하기 위한 의도적 동작). 로그에 `failed to load …/auth.json`이 남는다. `AUTH_TOKENS_JSON` 환경 변수의 토큰은 이 영향을 받지 않는다.

## 5. 주의

- `config/auth.json`은 `.gitignore`에 있고 git 추적에서도 제외됐다(PR #18). 토큰 값을 커밋하지 않는다. 형식 예시는 `config/auth.json.example`. 추적 해제 커밋을 서버에서 `git pull`하면 서버의 파일이 삭제될 수 있으니 pull 전에 백업한다.
- 토큰을 로그·티켓·채팅에 남기지 않는다. 유출 시 즉시 `--revoke`.

## 6. admin 화면에서 토큰 발급 (admin.html "API 토큰 발급")

`config/auth.json`은 api 컨테이너에 읽기 전용으로 마운트돼 있어 화면에서는 고칠 수 없다. 화면(`/v1/auth/tokens`)은
별도 저장소 `data/auth_tokens.json`을 쓴다.

- **해시만 저장:** 파일에는 토큰의 sha256 해시만 남는다. 토큰 원문은 발급 응답에 **한 번만** 나오고 다시 볼 수 없다
  (목록에는 이름·롤·출처·지문만). 같은 파일을 직접 편집해 토큰을 넣을 수는 없다(해시 키만 읽는다).
- **조건:** admin 롤 필요. **`AUTH_MODE=off`에서는 발급·회수가 409로 막힌다**(누구나 접근 가능한 상태에서 토큰을
  미리 만들 수 없게). 그래서 첫 admin 토큰은 호스트에서 만든다:
  `python3 scripts/issue_system_token.py --name <이름> --roles admin --allow-privileged` → `AUTH_MODE=apikey`로 켠 뒤
  그 토큰으로 화면에 로그인해 나머지를 발급한다.
- **롤:** 기본 author. senior/admin은 `allow_privileged` 확인이 필요하고, `<시스템>@<판>` 형식 이름에는 줄 수 없다.
- **교체/회수:** 같은 이름은 `rotate`를 켜야 교체(기존 토큰은 즉시 폐기), 회수는 즉시 반영된다(재시작 불필요).
  `config/auth.json`·환경변수 토큰은 화면에서 조회만 되며 회수는 호스트에서 `issue_system_token.py --revoke`로 한다.
  자기 자신의 토큰은 회수할 수 없다(잠김 방지).
- 로그에는 이름·롤·발급자·지문만 남고 토큰은 남지 않는다.

## 7. 크론 작업 상태 표시 (admin.html "크론 작업 상태")

api 컨테이너는 호스트 crontab을 볼 수 없다. 호스트에서 스냅샷 스크립트를 5분 주기로 등록하면 `logs/cron_status.json`이
갱신되고 화면이 이를 읽는다(`GET /v1/ops/cron`, admin).

```
*/5 * * * * /home/citec/dev/citec-kb/scripts/cron_snapshot.py >> /home/citec/dev/citec-kb/logs/cron_snapshot.log 2>&1
```

- 상태: `running` / `ok` / `failed` / `missed` / `unknown`. cron은 종료 코드를 남기지 않으므로 `failed`는 로그 끝의
  오류 표식(❌, Traceback, ERROR, 실패)으로 추정한 값이고, `missed`는 직전 예정 시각 이후 로그가 갱신되지 않았다는 뜻이다.
  `>> 파일` 리다이렉트가 없는 job은 `unknown`.
- 수행이력은 스크립트가 `<이름>_<STAMP>.log`로 남기는 최근 5건(`ingest_*.log`, `confluence_sync_*.log` 등)을 보여준다.
- 스냅샷이 15분 넘게 갱신되지 않으면 화면에 "갱신 중단 의심"이 표시된다. 스냅샷 파일이 없으면 등록 안내가 나온다.
