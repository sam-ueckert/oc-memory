#!/usr/bin/env bash
# oc-memory setup script
# Sets up oc-memory locally (non-Docker) and prints MCP config for Claude Code / Cursor / OpenClaw

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         oc-memory v2 setup               ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Check Python version ─────────────────────────────────────────────────────

PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
  echo -e "${RED}✗ Python not found. Install Python 3.11+ and retry.${NC}"
  exit 1
fi

PYVER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYMAJ=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PYMIN=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 11 ]; }; then
  echo -e "${RED}✗ Python 3.11+ required (found $PYVER).${NC}"
  exit 1
fi
echo -e "${GREEN}✓ Python $PYVER${NC}"

# ── Create data directory ─────────────────────────────────────────────────────

DATA_DIR="${OC_MEMORY_HOME:-$HOME/.oc-memory}"
mkdir -p "$DATA_DIR"
echo -e "${GREEN}✓ Data directory: $DATA_DIR${NC}"

# ── Install ───────────────────────────────────────────────────────────────────

echo ""
echo "Installing oc-memory..."
echo ""

if command -v uv &>/dev/null; then
  echo "Using uv..."
  uv pip install -e "$(dirname "$0")"
else
  echo "Using pip..."
  "$PYTHON" -m pip install -e "$(dirname "$0")"
fi

echo ""
echo -e "${GREEN}✓ oc-memory installed${NC}"

# ── Download ONNX model ────────────────────────────────────────────────────────

echo ""
echo "Downloading ONNX embedding model (bge-small-en-v1.5, ~24MB)..."
"$PYTHON" -c "
from oc_memory.embedding_backends import download_onnx_model, is_model_downloaded
if is_model_downloaded():
    print('  (already cached, skipping)')
else:
    p = download_onnx_model()
    print(f'  Saved to {p}')
"
echo -e "${GREEN}✓ Embedding model ready${NC}"

# ── Print MCP config ──────────────────────────────────────────────────────────

echo ""
oc-memory setup

# ── Offer to patch ~/.claude.json ────────────────────────────────────────────

CLAUDE_JSON="$HOME/.claude.json"
if [ -f "$CLAUDE_JSON" ]; then
  echo ""
  echo -e "${YELLOW}Claude Code detected at $CLAUDE_JSON${NC}"
  echo -n "Auto-patch ~/.claude.json with oc-memory MCP config? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    "$PYTHON" - <<'EOF'
import json, subprocess, sys, os
from pathlib import Path

claude_json = Path.home() / ".claude.json"
config = json.loads(claude_json.read_text())

# Get the MCP config entry
result = subprocess.run(
    ["oc-memory", "config", "--client", "claude"],
    capture_output=True, text=True
)
snippet = json.loads(result.stdout)
entry = snippet["mcpServers"]["oc-memory"]

if "mcpServers" not in config:
    config["mcpServers"] = {}

if "oc-memory" in config["mcpServers"]:
    print("  oc-memory already in ~/.claude.json — skipping")
else:
    config["mcpServers"]["oc-memory"] = entry
    claude_json.write_text(json.dumps(config, indent=2))
    print("  ✓ Patched ~/.claude.json")
    print("  Restart Claude Code to activate.")
EOF
  fi
fi

# ── Offer to install mem CLI ─────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MEM_CLI="$SCRIPT_DIR/cli/mem"

if [ -f "$MEM_CLI" ]; then
  echo ""
  echo -e "${YELLOW}mem CLI wrapper available (cli/mem)${NC}"
  echo -n "Install mem CLI to ~/bin/mem? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/bin"
    cp "$MEM_CLI" "$HOME/bin/mem"
    chmod +x "$HOME/bin/mem"
    echo -e "${GREEN}✓ Installed to ~/bin/mem${NC}"
    if ! echo "$PATH" | grep -q "$HOME/bin"; then
      echo -e "${YELLOW}Note: Add ~/bin to your PATH:${NC}"
      echo "  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.bashrc"
      echo "  source ~/.bashrc"
    fi
    echo ""
    echo "Quick test:"
    echo "  mem stats"
    echo "  mem search 'first memory'"
  fi
fi

echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Quick start:"
echo "  oc-memory store --scene myproject --type fact --salience 0.8 'My first memory'"
echo "  oc-memory search 'first memory'"
echo "  oc-memory stats"
echo ""
