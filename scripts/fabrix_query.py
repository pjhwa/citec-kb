#!/usr/bin/env python3
"""Fabrix 한 번 호출해 보는 시험용 스크립트 (표준 라이브러리만 사용, 앱 코드 불필요).

401 같은 인증 문제를 앱과 분리해서 확인할 때 쓴다. 키 값은 출력하지 않고 길이와 앞뒤 2글자만 보여 준다.

  # 운영 서버: .env의 COMPANY_* 값을 읽어 실행
  python3 scripts/fabrix_query.py
  python3 scripts/fabrix_query.py "서버 재부팅 원인을 한 줄로 설명해줘"

  # 컨테이너가 쓰는 환경 그대로 확인하려면
  docker compose exec api python3 /app/scripts/fabrix_query.py   # scripts가 마운트돼 있을 때

  # 토큰 형식 점검: 앱은 COMPANY_PASS_KEY를 그대로 x-openapi-token에 넣는다.
  python3 scripts/fabrix_query.py --bearer      # "Bearer " 접두를 붙여 시도

환경 변수: COMPANY_ENDPOINT_URL, COMPANY_CLIENT_KEY, COMPANY_PASS_KEY, COMPANY_EMAIL, COMPANY_MODEL_ID(기본 glm-5.2)
없으면 --env-file(기본 ./.env)에서 읽는다.
종료 코드: 0 성공, 1 오류 응답/빈 응답, 2 설정 누락.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KEYS = ("COMPANY_ENDPOINT_URL", "COMPANY_CLIENT_KEY", "COMPANY_PASS_KEY", "COMPANY_EMAIL")


def load_env(path: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.startswith("COMPANY_")}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k.startswith("COMPANY_") and k not in env and v:
                env[k] = v
    return env


def mask(v: str) -> str:
    if not v:
        return "(비어 있음)"
    return f"len={len(v)} {v[:2]}…{v[-2:]}" if len(v) > 6 else f"len={len(v)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", default="안녕하세요. 한 문장으로 자기소개를 해 주세요.")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--bearer", action="store_true", help="x-openapi-token에 'Bearer ' 접두를 붙인다")
    ap.add_argument("--max-tokens", type=int, default=256, help="답변 토큰(추론 여유 2048은 자동 추가)")
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args()

    env = load_env(a.env_file)
    missing = [k for k in KEYS if not env.get(k)]
    print("설정:")
    for k in KEYS:
        print(f"  {k} = {env.get(k, '') if k in ('COMPANY_ENDPOINT_URL', 'COMPANY_EMAIL') else mask(env.get(k, ''))}")
    model = env.get("COMPANY_MODEL_ID", "glm-5.2")
    print(f"  COMPANY_MODEL_ID = {model}")
    if missing:
        print(f"누락: {', '.join(missing)}", file=sys.stderr)
        return 2

    token = env["COMPANY_PASS_KEY"]
    if a.bearer and not token.lower().startswith("bearer "):
        token = "Bearer " + token
    url = env["COMPANY_ENDPOINT_URL"].rstrip("/") + "/openapi/chat/v1/messages"
    body = {
        "modelIds": [model],
        "contents": [a.prompt],
        "llmConfig": {"max_new_tokens": a.max_tokens + 2048, "temperature": 0.2},
        "isStream": False,
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "x-fabrix-client": env["COMPANY_CLIENT_KEY"],
        "x-openapi-token": token,
        "x-generative-ai-user-email": env["COMPANY_EMAIL"],
        "content-type": "application/json",
    })
    print(f"\nPOST {url}\n질문: {a.prompt}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"\n연결 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"HTTP {status} ({time.time() - t0:.1f}s)")

    try:
        data = json.loads(raw)
    except ValueError:
        print(raw[:500])
        return 1
    if status != 200:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:800])
        if status == 401:
            print("\n→ 인증 거절입니다. 클라이언트 키/패스 키/이메일 값과 만료 여부를 확인하세요."
                  " 패스 키 형식이 의심되면 --bearer로도 시도해 보세요.", file=sys.stderr)
        return 1
    text = str(data.get("content") or "")
    print(f"status={data.get('status')} finishReason={data.get('finishReason')} truncated={data.get('truncated')}")
    print("\n답변:\n" + (text or "(빈 응답 — --max-tokens를 늘리거나 filterBlockReason 확인)"))
    return 0 if text else 1


if __name__ == "__main__":
    sys.exit(main())
