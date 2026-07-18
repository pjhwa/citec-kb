#!/bin/sh
set -e
echo "[api] running alembic upgrade head..."
alembic upgrade head
echo "[api] migrations done; starting uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
