FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY apps/worker/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY apps/worker/app /app/app
COPY config /app/config

ENV PYTHONPATH=/app

CMD ["python", "-m", "app.main"]
