#!/usr/bin/env bash
# daily-memory-prune.sh — Summarize and prune daily memory markdown logs
#
# Usage:
#   OC_MEMORY_WORKSPACE=/path/to/workspace bash scripts/daily-memory-prune.sh [date]
#
# Environment:
#   OC_MEMORY_WORKSPACE  Root workspace directory (default: current directory)
#   OC_MEMORY_DB         Path to SQLite database (default: ~/.oc-memory/memory.db)
#
# Schedule (cron example — every 30min):
#   */30 * * * * OC_MEMORY_WORKSPACE=/path/to/workspace bash /path/to/scripts/daily-memory-prune.sh

set -euo pipefail

WORKSPACE="${OC_MEMORY_WORKSPACE:-$(pwd)}"
DATE="${1:-$(date +%Y-%m-%d)}"
MEMORY_DIR="$WORKSPACE/memory"
LOG_FILE="$MEMORY_DIR/$DATE.md"

# Minimum size/section thresholds before pruning
MIN_SIZE_KB=2
MIN_SECTIONS=2

# ── Pre-flight checks ─────────────────────────────────────────────────────────

if [ ! -d "$MEMORY_DIR" ]; then
  echo "Memory directory not found: $MEMORY_DIR"
  exit 0
fi

if [ ! -f "$LOG_FILE" ]; then
  echo "No memory log for $DATE: $LOG_FILE"
  exit 0
fi

# ── Size and section check ────────────────────────────────────────────────────

FILE_SIZE_KB=$(du -k "$LOG_FILE" | awk '{print $1}')
SECTION_COUNT=$(grep -c '^## ' "$LOG_FILE" 2>/dev/null || echo 0)

if [ "$FILE_SIZE_KB" -lt "$MIN_SIZE_KB" ]; then
  echo "[$DATE] File too small (${FILE_SIZE_KB}KB < ${MIN_SIZE_KB}KB) — skipping prune"
  exit 0
fi

if [ "$SECTION_COUNT" -lt "$MIN_SECTIONS" ]; then
  echo "[$DATE] Too few sections ($SECTION_COUNT < $MIN_SECTIONS) — skipping prune"
  exit 0
fi

echo "[$DATE] Pruning memory log (${FILE_SIZE_KB}KB, $SECTION_COUNT sections)"

# ── Summarize into oc-memory DB ───────────────────────────────────────────────

if command -v oc-memory &>/dev/null; then
  echo "[$DATE] Extracting memory cells from $LOG_FILE"
  oc-memory extract-file "$LOG_FILE" 2>/dev/null || echo "  (extraction skipped — Ollama not available)"
else
  echo "  (oc-memory not found — skipping extraction)"
fi

# ── Truncate the log file (keep last 10 lines as context) ─────────────────────

TAIL_LINES=$(tail -10 "$LOG_FILE")
cat > "$LOG_FILE" <<EOF
# Memory Log — $DATE (pruned $(date +%Y-%m-%dT%H:%M:%S))

*This file was pruned. Full content extracted into oc-memory database.*

## Last entries before prune

$TAIL_LINES
EOF

echo "[$DATE] Log pruned. Cells in DB: $(oc-memory stats 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("total_cells","?"))' 2>/dev/null || echo "?")"

# ── Git commit (if inside a git repo) ────────────────────────────────────────

if git -C "$WORKSPACE" rev-parse --git-dir &>/dev/null; then
  git -C "$WORKSPACE" add "$LOG_FILE" 2>/dev/null || true
  git -C "$WORKSPACE" commit -m "memory: prune $DATE log (extracted to DB)" --quiet 2>/dev/null || true
  echo "[$DATE] Committed pruned log"
fi
