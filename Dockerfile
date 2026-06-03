FROM python:3.11-slim

# Build args — pass OC_MEMORY_SSL_NO_VERIFY=1 on corporate networks:
#   nerdctl build --build-arg OC_MEMORY_SSL_NO_VERIFY=1 -t oc-memory .
ARG OC_MEMORY_SSL_NO_VERIFY=

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir -e ".[drive]"

# Pre-bake ONNX model — non-fatal; falls back to download on first use if skipped.
# Set build arg OC_MEMORY_SSL_NO_VERIFY=1 to bypass SSL inspection proxies.
RUN OC_MEMORY_SSL_NO_VERIFY="${OC_MEMORY_SSL_NO_VERIFY}" python3 -c \
    "from oc_memory.embedding_backends import download_onnx_model, is_model_downloaded; \
     download_onnx_model() if not is_model_downloaded() else print('model cached')" \
    || echo "ONNX model download failed — server will download on first use"

VOLUME /data
ENV OC_MEMORY_DB=/data/memory.db
ENV OC_MEMORY_EXPORT=/data/export
ENV MCP_TRANSPORT=http
ENV MCP_PORT=8765

EXPOSE 8765

CMD ["python3", "-m", "oc_memory.mcp_server", "--http"]
