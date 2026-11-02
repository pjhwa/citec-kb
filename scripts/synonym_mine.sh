#!/usr/bin/env bash
# api 컨테이너 안에서 python -m app.synonyms.cli 를 실행한다.
#   ./scripts/synonym_mine.sh mine --since-years 2 --out /tmp/syn-RUN.json
#   ./scripts/synonym_mine.sh import --file /tmp/syn-RUN.json
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICE="${SERVICE:-api}"
exec docker compose exec -T "$SERVICE" python -m app.synonyms.cli "$@"
