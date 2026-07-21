# OIDC / IdP 연동 가이드 (citec-kb)

## 모드 요약

| AUTH_MODE | 동작 |
|-----------|------|
| `off` | 오픈 파일럿 (기본). 쓰기 API 무인증 허용 |
| `apikey` | `config/auth.json` / `AUTH_TOKENS_JSON` + JWT |
| `oidc_stub` | `stub:user:roles` + JWT |
| `oidc` | **JWT 필수** (API key도 서비스 계정용으로 허용) |

## 로컬 mock IdP (RS256 JWKS) — 권장 개발 경로

API가 내장 mock IdP를 제공합니다.

- Discovery: `GET /v1/mock-idp/.well-known/openid-configuration`
- Authorize / Token / JWKS: `/v1/mock-idp/authorize|token|jwks`
- 기본 활성: `APP_ENV=dev|local|test` 또는 `MOCK_OIDC_ENABLED=true`

### 예시 `.env`

```bash
AUTH_MODE=oidc
MOCK_OIDC_ENABLED=true
OIDC_ISSUER=http://localhost:8573/v1/mock-idp
OIDC_CLIENT_ID=citec-kb
OIDC_CLIENT_SECRET=mock-secret
OIDC_AUDIENCE=citec-kb
OIDC_REDIRECT_URI=http://localhost:8573/v1/auth/callback
PUBLIC_WEB_BASE=http://localhost:8572
PUBLIC_API_BASE=http://localhost:8573
# OIDC_JWT_SECRET 는 비워 두어 RS256/JWKS 경로를 강제
```

### 플로우 확인

```bash
# 단위·e2e (컨테이너)
docker compose exec -T api python -m pytest /app/tests/test_mock_idp_e2e.py -q

# 인프로세스 스크립트
docker compose exec -T api python /app/../scripts/oidc_idp_e2e.py
# 또는 호스트에서 tests 경로로 pytest
```

브라우저: http://localhost:8572/login.html → **OIDC 로그인**  
(서버에 위 env가 적용된 경우)

Authorize 시 쿼리로 사용자 지정 가능:

```
.../authorize?...&sub=alice&roles=viewer,author,senior
```

## 로컬 Keycloak (compose profile) — 실 IdP 경로 검증

포트 범위 내 **8576**. mock IdP와 달리 실제 Keycloak JWKS/토큰을 사용합니다.

```bash
# 기동
docker compose --profile keycloak up -d keycloak

# 준비 대기 후 e2e (discovery · password grant · JWT · API RBAC)
.venv/bin/python scripts/keycloak_oidc_e2e.py --wait 180
```

| 항목 | 값 |
|------|-----|
| Admin console | http://localhost:8576 (admin / admin) |
| Issuer | `http://localhost:8576/realms/citec` |
| Client | `citec-kb` / secret `citec-kb-secret` |
| Users | `viewer`/`author`/`senior`/`admin` (비밀번호=username) |
| Realm import | `deploy/keycloak/realm-citec.json` |

API를 Keycloak에 붙이려면 (선택, 기본 stack은 `AUTH_MODE=off` 유지):

```bash
# .env 또는 compose override
AUTH_MODE=oidc
OIDC_ISSUER=http://localhost:8576/realms/citec
OIDC_CLIENT_ID=citec-kb
OIDC_CLIENT_SECRET=citec-kb-secret
OIDC_AUDIENCE=*
OIDC_REDIRECT_URI=http://localhost:8573/v1/auth/callback
# Access token에 aud 가 없을 수 있음 → OIDC_AUDIENCE=* 후 azp=client_id 검증
```

브라우저: Keycloak 로그인 UI → `http://localhost:8572/login.html` (API에 OIDC env 적용 후).

## Keycloak 실서버 체크리스트

1. Realm 생성 (예: `citec`)
2. Client `citec-kb`
   - Access type: confidential (또는 public + PKCE 추후)
   - Valid redirect URIs: `http://localhost:8573/v1/auth/callback`
   - Web origins: `http://localhost:8572`
3. Client roles 또는 realm roles: `viewer`, `author`, `senior`, `admin`  
   (또는 `kb-admin` 등 — API가 매핑)
4. 사용자에게 role assign
5. `.env`:

```bash
AUTH_MODE=oidc
OIDC_ISSUER=https://keycloak.example.com/realms/citec
OIDC_CLIENT_ID=citec-kb
OIDC_CLIENT_SECRET=...
OIDC_AUDIENCE=citec-kb   # 또는 account / 비우면 client_id
OIDC_REDIRECT_URI=http://localhost:8573/v1/auth/callback
OIDC_ROLE_CLAIM=roles    # Keycloak realm: realm_access.roles 자동 fallback
```

6. 확인

```bash
curl -sS $API/v1/auth/status | jq .oidc
# discovery_ok=true 여야 함
open http://localhost:8572/login.html
```

## Entra ID (Azure AD) 요약

- Issuer: `https://login.microsoftonline.com/<tenant>/v2.0`
- App registration redirect: `.../v1/auth/callback`
- Optional claims / App roles → `roles` claim
- `OIDC_AUDIENCE` = Application (client) ID URI 또는 client id

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/v1/auth/status` | 모드·discovery 상태 |
| GET | `/v1/auth/me` | Bearer principal |
| GET | `/v1/auth/login` | IdP redirect 또는 JSON URL |
| GET | `/v1/auth/callback` | code 교환 후 web fragment |
| GET | `/v1/auth/logout` | end_session 또는 web |
| POST | `/v1/auth/dev/token` | HS256 mint (`OIDC_JWT_SECRET`) |

## 보호 경로 (enforced 모드)

- Insight 작성/제출: `author+`
- Insight 승인/거절/reindex: `senior+`
- `POST /v1/jobs`: `admin`
