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

# ── Offer to install context-digest skill ────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIGEST_SCRIPT="$SCRIPT_DIR/skills/context-digest/scripts/gen-context-digest.sh"

if [ -f "$DIGEST_SCRIPT" ]; then
  echo ""
  echo -e "${YELLOW}Optional: Context Digest (pre-loads memories into agent context)${NC}"
  echo "Generates a Memory Digest section in MEMORY.md or CLAUDE.md so every"
  echo "agent session starts with high-salience memories in context."
  echo -n "Install context-digest to ~/bin? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/bin"
    cp "$DIGEST_SCRIPT" "$HOME/bin/gen-context-digest.sh"
    chmod +x "$HOME/bin/gen-context-digest.sh"
    echo -e "${GREEN}✓ Installed to ~/bin/gen-context-digest.sh${NC}"
    echo ""
    echo "  Test:  WORKSPACE=/path/to/workspace bash ~/bin/gen-context-digest.sh"
    echo "  Cron:  0 */3 * * * WORKSPACE=/path/to/workspace bash ~/bin/gen-context-digest.sh"
    echo ""
    echo "  Add markers to your MEMORY.md or CLAUDE.md:"
    echo "    <!-- ARCHY_DIGEST_START -->"
    echo "    <!-- ARCHY_DIGEST_END -->"
  fi
fi

# ── Offer to install promote-lessons skill ────────────────────────────────────

PROMOTE_SCRIPT="$SCRIPT_DIR/skills/promote-lessons/scripts/promote-lessons.sh"

if [ -f "$PROMOTE_SCRIPT" ]; then
  echo ""
  echo -e "${YELLOW}Optional: Promote Lessons (learning loop — corrections become rules)${NC}"
  echo "Synthesizes behavioral rules from tagged corrections and lessons via LLM,"
  echo "then injects them into SOUL.md or CLAUDE.md. Works with OpenClaw and Anthropic API."
  echo -n "Install promote-lessons to ~/bin? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/bin"
    cp "$PROMOTE_SCRIPT" "$HOME/bin/promote-lessons.sh"
    chmod +x "$HOME/bin/promote-lessons.sh"
    echo -e "${GREEN}✓ Installed to ~/bin/promote-lessons.sh${NC}"
    echo ""
    echo "  OpenClaw:  API_TOKEN=<gateway-token> WORKSPACE=/path bash ~/bin/promote-lessons.sh"
    echo "  Claude:    API_TOKEN=\$ANTHROPIC_API_KEY API_URL=https://api.anthropic.com/v1/messages WORKSPACE=/path bash ~/bin/promote-lessons.sh"
    echo ""
    echo "  Add markers to your SOUL.md or CLAUDE.md:"
    echo "    <!-- LEARNED_RULES_START -->"
    echo "    ## Learned Rules"
    echo "    *No rules yet.*"
    echo "    <!-- LEARNED_RULES_END -->"
  fi
fi

# ── Offer to enable multi-user isolation ─────────────────────────────────────

echo ""
echo -e "${YELLOW}Optional: Multi-user isolation (owner_id + visibility per cell)${NC}"
echo "Adds per-user ownership to all memory cells. Required when multiple users"
echo "share the same memory server. Set OC_MEMORY_ADMIN_USER to your user ID to"
echo "bypass ownership filters as admin."
echo -n "Enable multi-user? [y/N] "
read -r REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
  echo ""
  echo "  Add this to your shell rc (e.g. ~/.bashrc or ~/.zshrc):"
  echo ""
  echo '    export OC_MEMORY_ADMIN_USER="<your-user-id>"'
  echo ""
  echo "  Example (use your actual user/agent ID):"
  echo '    export OC_MEMORY_ADMIN_USER="u0am4blbuuw"'
  echo ""

  DB_FILE="${OC_MEMORY_DB:-$DATA_DIR/memory.db}"
  if [ -f "$DB_FILE" ]; then
    echo "  Existing database found at $DB_FILE."
    echo -n "  Run migrate-ownership.py to backfill owner_id on existing cells? [y/N] "
    read -r MIGRATE_REPLY
    if [[ "$MIGRATE_REPLY" =~ ^[Yy]$ ]]; then
      echo -n "  Your admin user ID: "
      read -r ADMIN_ID
      if [ -n "$ADMIN_ID" ]; then
        "$PYTHON" "$SCRIPT_DIR/scripts/migrate-ownership.py" --admin-user "$ADMIN_ID" --db "$DB_FILE"
      else
        echo "  Skipping — no user ID provided"
      fi
    fi
  else
    echo "  (No existing database to migrate)"
  fi
fi

# ── Offer to set up Google Drive backup ───────────────────────────────────────

echo ""
echo -e "${YELLOW}Optional: Google Drive backup (uploads memory.db + export to Drive)${NC}"
echo "Requires: pip install oc-memory[drive] + Google OAuth2 credentials"
echo -n "Set up Google Drive backup? [y/N] "
read -r REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
  echo ""
  echo "  Installing Drive dependencies..."
  if command -v uv &>/dev/null; then
    uv pip install -e "$SCRIPT_DIR[drive]"
  else
    "$PYTHON" -m pip install -e "$SCRIPT_DIR[drive]"
  fi
  echo ""
  echo "  To set up OAuth2 credentials:"
  echo "  1. Go to https://console.cloud.google.com/apis/credentials"
  echo "  2. Create an OAuth 2.0 Client ID → Desktop application"
  echo "  3. Download the JSON and save to:"
  echo "     ${OC_MEMORY_DRIVE_CLIENT_CREDS:-$DATA_DIR/drive-client-creds.json}"
  echo ""
  echo "  On first use (oc-memory backup-drive), a browser window will open for authorization."
  echo "  Token saved to: ${OC_MEMORY_DRIVE_TOKEN:-$DATA_DIR/drive-token.json}"
  echo ""
  echo "  Usage: oc-memory backup-drive"
fi

# ── Offer to install cron jobs ────────────────────────────────────────────────

if [ -f "$SCRIPT_DIR/scripts/install-crons.sh" ]; then
  echo ""
  echo -e "${YELLOW}Optional: Install cron jobs (context-digest, daily-prune, promote-lessons)${NC}"
  echo -n "Install cron jobs? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    bash "$SCRIPT_DIR/scripts/install-crons.sh" --workspace "${OC_MEMORY_WORKSPACE:-$(pwd)}"
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
