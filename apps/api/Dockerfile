FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_OFFLINE=1 \
    EMBED_TORCH_THREADS=4 \
    EMBED_ENCODE_BATCH=16

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt /app/requirements.txt
# CPU torch first (smaller than default CUDA wheels), then the rest.
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r /app/requirements.txt

COPY apps/api/app /app/app
COPY apps/api/alembic.ini /app/alembic.ini
COPY apps/api/alembic /app/alembic
COPY config /app/config
COPY packages/domain /app/packages/domain
COPY scripts/api-entrypoint.sh /app/api-entrypoint.sh
RUN chmod +x /app/api-entrypoint.sh

ENV PYTHONPATH=/app:/app/packages/domain

EXPOSE 8000
ENTRYPOINT ["/app/api-entrypoint.sh"]
