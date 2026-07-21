FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /app
USER mcp

ENV CITEC_KB_BASE_URL=http://api:8000 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8100 \
    MCP_TRANSPORT=streamable-http

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import socket; s=socket.create_connection(('127.0.0.1', 8100), timeout=8); s.close()"

CMD ["python3", "server.py"]
