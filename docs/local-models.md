# Local Model Setup

oc-memory uses [Ollama](https://ollama.com) for two optional features:

- **Vector embeddings** — semantic similarity search (via `nomic-embed-text`)
- **LLM extraction** — automatic parsing of text into typed memory cells (via `llama3.2:3b` or similar)

Both are optional. Without Ollama, oc-memory falls back to FTS5 full-text search, which is fast and works well for keyword-based recall.

## System Requirements

### Minimum (FTS only, no Ollama)

- 1 CPU core, 512MB RAM
- Python 3.11+
- Works on any Linux, macOS, or WSL

### Recommended (with Ollama)

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 4GB | 8GB+ |
| CPU | 4 cores | 8+ cores |
| Disk | 5GB free | 10GB+ |
| GPU | Not required | NVIDIA GPU speeds up inference significantly |

Ollama models are CPU-capable but slow. A dedicated machine with more RAM is ideal if you plan to use extraction and embedding heavily.

### Split Architecture

If your OpenClaw host is resource-constrained (e.g., a 2GB VPS), run Ollama on a separate machine:

```
┌─────────────┐         ┌──────────────────┐
│  OCP (VPS)  │   SSH   │  GPU/Big Server   │
│  OpenClaw   │ ──────> │  Ollama           │
│  oc-memory  │   HTTP  │  nomic-embed-text │
│  SQLite DB  │ <────── │  llama3.2:3b      │
└─────────────┘         └──────────────────┘
```

Set `OLLAMA_URL` to point at the remote host (e.g., `http://my-gpu-server:11434`). If using SSH tunnels:

```bash
# Forward Ollama port from remote to local
ssh -L 11434:localhost:11434 user@my-gpu-server -N &
export OLLAMA_URL=http://localhost:11434
```

## Installing Ollama

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

## Required Models

### nomic-embed-text (embeddings)

768-dimensional embeddings, ~274MB download.

```bash
ollama pull nomic-embed-text
```

Test:
```bash
curl http://localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text","input":"test embedding"}'
```

### llama3.2:3b (extraction)

3B parameter model for parsing text into structured cells. ~2GB download.

```bash
ollama pull llama3.2:3b
```

Test:
```bash
curl http://localhost:11434/api/generate \
  -d '{"model":"llama3.2:3b","prompt":"Say hello","stream":false}'
```

### Alternative Models

You can swap models via environment variables:

| Variable | Default | Alternatives |
|----------|---------|-------------|
| Embedding model | `nomic-embed-text` | `mxbai-embed-large`, `all-minilm` |
| Extraction model | `llama3.2:3b` | `llama3.2:1b` (faster, less accurate), `mistral` (7B, better quality) |

Smaller models are faster but extract fewer/lower-quality cells. Larger models are more thorough but need more RAM and time.

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

**"Ollama not available"** — The server isn't running or isn't reachable at the configured URL.
```bash
curl http://localhost:11434/api/tags  # should return model list
```

**Slow embedding/extraction** — Normal on CPU. nomic-embed-text takes ~1-3s per embedding on CPU. Extraction with llama3.2:3b takes 10-60s per text block. Use FTS search as the fast default.

**Out of memory** — Reduce model size (`llama3.2:1b` instead of `:3b`) or increase swap. Ollama loads models into RAM.

**Remote Ollama unreachable** — Check firewall rules. Ollama binds to `127.0.0.1` by default. Set `OLLAMA_HOST=0.0.0.0` to listen on all interfaces, or use an SSH tunnel.
