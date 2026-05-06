#!/usr/bin/env bash
# gen-context-digest.sh — Pull high-salience Archy cells and inject into a MEMORY.md file.
#
# Usage:
#   gen-context-digest.sh [MEMORY_FILE] [MEM_CMD]
#
# Defaults:
#   MEMORY_FILE  — $WORKSPACE/MEMORY.md  (WORKSPACE env var, or cwd)
#   MEM_CMD      — mem  (must be on PATH)
#
# The script replaces (or appends) a block bounded by HTML markers:
#   <!-- ARCHY_DIGEST_START --> ... <!-- ARCHY_DIGEST_END -->
#
# Designed to run on cron every 2-3h. Output is ~50 lines.

set -euo pipefail

MEMORY_FILE="${1:-${WORKSPACE:-.}/MEMORY.md}"
MEM_CMD="${2:-mem}"
MARKER_START="<!-- ARCHY_DIGEST_START -->"
MARKER_END="<!-- ARCHY_DIGEST_END -->"

log() { echo "[gen-context-digest] $*" >&2; }

# Verify mem is available
if ! command -v "$MEM_CMD" &>/dev/null; then
  echo "[gen-context-digest] ERROR: '$MEM_CMD' not found on PATH. Install oc-memory or set MEM_CMD." >&2
  exit 1
fi

if [[ ! -f "$MEMORY_FILE" ]]; then
  echo "[gen-context-digest] ERROR: MEMORY_FILE not found: $MEMORY_FILE" >&2
  exit 1
fi

log "Querying memory store..."

# Pull top cells by scene — adjust scenes to match your setup
LESSONS=$(    "$MEM_CMD" scene "lessons"        2>/dev/null | grep -E "sal:(0\.[89]|1\.0)" | head -8  || true)
DECISIONS=$(  "$MEM_CMD" scene "config"         2>/dev/null | grep -E "\[decision\].*sal:(0\.[89]|1\.0)" | head -6 || true)
INFRA=$(      "$MEM_CMD" scene "infrastructure" 2>/dev/null | grep -E "sal:(0\.[89]|1\.0)" | head -5  || true)
PROJECTS=$(   "$MEM_CMD" scene "projects"       2>/dev/null | grep -E "sal:(0\.[89]|1\.0)" | head -4  || true)
PATTERNS=$(   "$MEM_CMD" scene "foreman"        2>/dev/null | grep -E "sal:(0\.[89]|1\.0)" | head -3  || true)
PEOPLE=$(     "$MEM_CMD" scene "commodore"      2>/dev/null | grep -E "sal:(0\.[89]|1\.0)" | head -3  || true)

# Strip cell IDs/types: "  [24] [decision] scene:X sal:1.00 — content" → "- content"
strip_ids() {
  sed -E 's/^[[:space:]]*\[[0-9]+\] \[[a-z]+\] (scene:[^ ]+ )?sal:[0-9.]+ — //' \
  | cut -c1-120 \
  | awk '!seen[$0]++' \
  | grep -v '^[[:space:]]*$' \
  | sed 's/^/- /'
}

TIMESTAMP=$(date '+%Y-%m-%d %H:%M %Z')

DIGEST=$(cat <<DIGEST_EOF
$MARKER_START
## Memory Digest
*Auto-generated $TIMESTAMP — top memory cells by salience*

### Lessons & Decisions
$(printf '%s\n%s' "$LESSONS" "$DECISIONS" | strip_ids)

### Infrastructure
$(echo "$INFRA" | strip_ids)

### Active Projects
$(echo "$PROJECTS" | strip_ids)

### Patterns & Sub-Agent Notes
$(echo "$PATTERNS" | strip_ids)

### People & Preferences
$(echo "$PEOPLE" | strip_ids)
$MARKER_END
DIGEST_EOF
)

if grep -q "$MARKER_START" "$MEMORY_FILE" 2>/dev/null; then
  python3 - "$MEMORY_FILE" "$MARKER_START" "$MARKER_END" "$DIGEST" <<'PYEOF'
import sys, re
path, start, end, new_block = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
content = open(path).read()
pattern = re.escape(start) + r'.*?' + re.escape(end)
updated = re.sub(pattern, new_block, content, flags=re.DOTALL)
open(path, 'w').write(updated)
PYEOF
  log "Updated existing digest section in $MEMORY_FILE"
else
  printf '\n\n%s\n' "$DIGEST" >> "$MEMORY_FILE"
  log "Appended digest section to $MEMORY_FILE"
fi

log "Done."
