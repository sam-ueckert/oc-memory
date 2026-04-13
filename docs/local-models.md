# Local Model Setup

oc-memory uses two optional backends for AI features:

- **Vector embeddings** — semantic similarity search
- **LLM extraction** — automatic parsing of text into typed memory cells (via Ollama + `llama3.2:3b`)

## Embedding Backends

### Primary: ONNX bge-small-en-v1.5 (default, zero config)

oc-memory ships with an ONNX embedding backend baked into the Docker image. **No external service required.**

- Model: `BAAI/bge-small-en-v1.5`
- Dimensions: **384**
- Runtime: ONNX Runtime (CPU-only, no GPU needed)
- Memory: ~120MB model weight, works on 512MB RAM
- Speed: ~50–200ms per embedding on a single CPU core

This is the default. No configuration needed — just run oc-memory and embeddings work.

### Optional: Ollama nomic-embed-text

If you prefer Ollama for embeddings (e.g., to use a different model or share an existing Ollama instance), set:

```bash
export OLLAMA_URL=http://localhost:11434
```

When `OLLAMA_URL` is set, oc-memory switches to the Ollama backend using `nomic-embed-text` (768-dim).

> ⚠️ **Dimension mismatch**: ONNX produces 384-dim vectors; Ollama nomic-embed-text produces 768-dim vectors. Do not mix backends on the same database — re-embed all cells if you switch.

## System Requirements

### Minimum (ONNX embeddings, no Ollama)

- 1 CPU core, 512MB RAM
- Python 3.11+
- Works on any Linux, macOS, or WSL — including low-power devices (Raspberry Pi, VPS)

### With Ollama (extraction + optional Ollama embeddings)

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 4GB | 8GB+ |
| CPU | 4 cores | 8+ cores |
| Disk | 5GB free | 10GB+ |
| GPU | Not required | NVIDIA GPU speeds up inference significantly |

Ollama models are CPU-capable but slow. A dedicated machine with more RAM is ideal if you plan to use extraction heavily.

### Split Architecture

If your OpenClaw host is resource-constrained (e.g., a 2GB VPS), run Ollama on a separate machine:

```
┌─────────────┐         ┌──────────────────┐
│  OCP (VPS)  │   HTTP  │  GPU/Big Server   │
│  OpenClaw   │ ──────> │  Ollama           │
│  oc-memory  │         │  llama3.2:3b      │
│  SQLite DB  │ <────── │  (extraction)     │
│  ONNX embed │         └──────────────────┘
└─────────────┘
```

Set `OLLAMA_URL` to point at the remote host (e.g., `http://my-gpu-server:11434`). If using SSH tunnels:

```bash
# Forward Ollama port from remote to local
ssh -L 11434:localhost:11434 user@my-gpu-server -N &
export OLLAMA_URL=http://localhost:11434
```

## Installing Ollama (for LLM extraction)

Ollama is only needed if you want automatic LLM-based extraction of structured cells from raw text.

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### macOS

```bash
brew install ollama
# or download from https://ollama.com/download
```

### Verify

```bash
ollama --version
ollama serve &  # start the server (or use systemd)
```

## Models

### llama3.2:3b (extraction — required for `oc-memory extract`)

3B parameter model for parsing text into structured cells. ~2GB download.

```bash
ollama pull llama3.2:3b
```

Test:
```bash
curl http://localhost:11434/api/generate \
  -d '{"model":"llama3.2:3b","prompt":"Say hello","stream":false}'
```

### nomic-embed-text (Ollama embeddings — optional)

768-dimensional embeddings, ~274MB download. Only needed if you set `OLLAMA_URL` and want Ollama to handle embeddings instead of the built-in ONNX backend.

```bash
ollama pull nomic-embed-text
```

Test:
```bash
curl http://localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text","input":"test embedding"}'
```

### Model Comparison

| Backend | Model | Dimensions | RAM | Config |
|---------|-------|-----------|-----|--------|
| ONNX (default) | bge-small-en-v1.5 | **384** | ~512MB | none |
| Ollama (opt-in) | nomic-embed-text | 768 | ~4GB | set `OLLAMA_URL` |

### Alternative Extraction Models

You can swap the extraction model via environment variables:

| Variable | Default | Alternatives |
|----------|---------|-------------|
| Extraction model | `llama3.2:3b` | `llama3.2:1b` (faster, less accurate), `mistral` (7B, better quality) |

## Running Ollama as a Service

### systemd (Linux)

Ollama's installer typically creates a systemd service. If not:

```bash
sudo tee /etc/systemd/system/ollama.service > /dev/null <<EOF
[Unit]
Description=Ollama LLM Server
After=network.target

[Service]
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=3
Environment=OLLAMA_HOST=0.0.0.0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ollama
```

### User-level service (no root)

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/ollama.service <<EOF
[Unit]
Description=Ollama LLM Server

[Service]
ExecStart=%h/.local/bin/ollama serve
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ollama
```

## Troubleshooting

**"Ollama not available"** — The server isn't running or isn't reachable at the configured URL. Only needed for extraction.
```bash
curl http://localhost:11434/api/tags  # should return model list
```

**ONNX embeddings slow** — Normal on very low-end hardware. bge-small-en-v1.5 is one of the fastest models available (~50ms on a Pi 4).

**Slow extraction** — Normal on CPU. llama3.2:3b takes 10–60s per text block. Use FTS search as the fast default — extraction is a background/batch operation.

**Out of memory (Ollama)** — Reduce model size (`llama3.2:1b` instead of `:3b`) or increase swap. Ollama loads models into RAM.

**Remote Ollama unreachable** — Check firewall rules. Ollama binds to `127.0.0.1` by default. Set `OLLAMA_HOST=0.0.0.0` to listen on all interfaces, or use an SSH tunnel.

**Switched backends, search broken** — If you switch from ONNX (384-dim) to Ollama (768-dim) or vice versa, existing vectors are incompatible. Re-embed all cells: `oc-memory embed --force`.
