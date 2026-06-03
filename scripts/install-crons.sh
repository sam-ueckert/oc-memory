#!/usr/bin/env bash
# install-crons.sh — Install optional oc-memory cron jobs
#
# Usage:
#   bash scripts/install-crons.sh [--workspace /path/to/workspace]
#
# Prompts for each cron job and installs only what you confirm.
# Safe to re-run — detects existing entries before adding.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

WORKSPACE="${1:-}"
if [ "$1" = "--workspace" ] && [ -n "${2:-}" ]; then
  WORKSPACE="$2"
fi
if [ -z "$WORKSPACE" ]; then
  WORKSPACE="$(pwd)"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "oc-memory cron installer"
echo "Workspace: $WORKSPACE"
echo ""

# Helper: safely add a cron entry (skip if already present)
add_cron() {
  local label="$1"
  local entry="$2"

  if crontab -l 2>/dev/null | grep -qF "$entry"; then
    echo -e "  ${YELLOW}already installed:${NC} $label"
    return
  fi

  (crontab -l 2>/dev/null; echo "$entry") | crontab -
  echo -e "  ${GREEN}✓ installed:${NC} $label"
}

# ── 1. Context digest cron (every 3h) ─────────────────────────────────────────

echo "1. Context Digest (gen-context-digest.sh)"
echo "   Rewrites the OC_MEMORY_DIGEST block in CLAUDE.md with high-salience cells."
echo "   Runs every 3 hours."
echo ""

DIGEST_SCRIPT="$HOME/bin/gen-context-digest.sh"
if [ ! -f "$DIGEST_SCRIPT" ]; then
  echo "   gen-context-digest.sh not found at $DIGEST_SCRIPT"
  echo "   Install it first: bash setup.sh → context-digest option"
  echo ""
else
  echo -n "   Install context-digest cron (every 3h)? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    ENTRY="0 */3 * * * OC_MEMORY_WORKSPACE=\"$WORKSPACE\" bash $DIGEST_SCRIPT >> /tmp/oc-memory-digest.log 2>&1"
    add_cron "context-digest (every 3h)" "$ENTRY"
  fi
fi

echo ""

# ── 2. Daily memory prune (every 30min) ───────────────────────────────────────

echo "2. Daily Memory Prune (daily-memory-prune.sh)"
echo "   Summarizes and prunes \$WORKSPACE/memory/YYYY-MM-DD.md logs."
echo "   Runs every 30 minutes."
echo ""

PRUNE_SCRIPT="$SCRIPT_DIR/daily-memory-prune.sh"
if [ ! -f "$PRUNE_SCRIPT" ]; then
  echo "   daily-memory-prune.sh not found at $PRUNE_SCRIPT"
  echo ""
else
  echo -n "   Install daily-prune cron (every 30min)? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    ENTRY="*/30 * * * * OC_MEMORY_WORKSPACE=\"$WORKSPACE\" bash $PRUNE_SCRIPT >> /tmp/oc-memory-prune.log 2>&1"
    add_cron "daily-memory-prune (every 30min)" "$ENTRY"
  fi
fi

echo ""

# ── 3. Promote lessons (weekly Sunday 3am) ────────────────────────────────────

echo "3. Promote Lessons (promote-lessons.sh)"
echo "   Synthesizes behavioral rules from tagged corrections via LLM."
echo "   Runs weekly on Sunday at 3am."
echo ""

LESSONS_SCRIPT="$HOME/bin/promote-lessons.sh"
if [ ! -f "$LESSONS_SCRIPT" ]; then
  echo "   promote-lessons.sh not found at $LESSONS_SCRIPT"
  echo "   Install it first: bash setup.sh → promote-lessons option"
  echo ""
else
  echo -n "   Install promote-lessons cron (Sunday 3am)? [y/N] "
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    echo ""
    echo "   API token for LLM calls:"
    echo "     - OpenClaw gateway: use your gateway token"
    echo "     - Anthropic API:    use \$ANTHROPIC_API_KEY"
    echo -n "   API token: "
    read -r API_TOKEN

    if [ -z "$API_TOKEN" ]; then
      echo "   Skipping — no API token provided"
    else
      ENTRY="0 3 * * 0 API_TOKEN=\"$API_TOKEN\" OC_MEMORY_WORKSPACE=\"$WORKSPACE\" bash $LESSONS_SCRIPT >> /tmp/oc-memory-lessons.log 2>&1"
      add_cron "promote-lessons (Sunday 3am)" "$ENTRY"
    fi
  fi
fi

echo ""
echo "Current crontab (oc-memory entries):"
crontab -l 2>/dev/null | grep -i "oc-memory\|gen-context-digest\|daily-memory-prune\|promote-lessons" || echo "  (none)"
echo ""
echo "Done."
