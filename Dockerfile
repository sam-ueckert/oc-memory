FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir -e ".[drive]"

# Pre-bake ONNX model (downloads to /root/.cache/oc-memory/models/)
RUN python3 -c "from oc_memory.embedding_backends import download_onnx_model; download_onnx_model()"

VOLUME /data
ENV OC_MEMORY_DB=/data/memory.db
ENV OC_MEMORY_EXPORT=/data/export
ENV MCP_TRANSPORT=http
ENV MCP_PORT=8765

EXPOSE 8765

CMD ["python3", "-m", "oc_memory.mcp_server", "--http"]
