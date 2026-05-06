#!/usr/bin/env bash
# promote-lessons.sh — Convert episodic memory (corrections/lessons) into procedural rules
# Queries mem for correction-tagged cells + high-salience lesson cells,
# synthesizes terse behavioral rules via LLM, and injects them into TARGET_FILE.
#
# Usage:
#   API_TOKEN=<token> bash promote-lessons.sh
#
# Env vars (all have defaults except API_TOKEN):
#   WORKSPACE    — workspace dir (default: cwd)
#   TARGET_FILE  — file to inject rules into (default: $WORKSPACE/SOUL.md)
#   MEM_CMD      — mem binary path (default: mem)
#   API_URL      — Chat Completions endpoint (default: http://127.0.0.1:18789/v1/chat/completions)
#   API_TOKEN    — Bearer token (REQUIRED — no default)
#   MAX_RULES    — max rules to keep (default: 30)

set -euo pipefail

export PATH="/home/swabby/bin:/home/swabby/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# --- Config ---
WORKSPACE="${WORKSPACE:-$(pwd)}"
TARGET_FILE="${TARGET_FILE:-$WORKSPACE/SOUL.md}"
MEM_CMD="${MEM_CMD:-mem}"
API_URL="${API_URL:-http://127.0.0.1:18789/v1/chat/completions}"
MAX_RULES="${MAX_RULES:-30}"

MARKER_START="<!-- LEARNED_RULES_START -->"
MARKER_END="<!-- LEARNED_RULES_END -->"

log() { echo "[promote-lessons] $*" >&2; }

# --- Validate ---
if [[ -z "${API_TOKEN:-}" ]]; then
  log "ERROR: API_TOKEN is required (no default)"
  exit 1
fi

if [[ ! -f "$TARGET_FILE" ]]; then
  log "ERROR: TARGET_FILE not found: $TARGET_FILE"
  exit 1
fi

if ! grep -q "$MARKER_START" "$TARGET_FILE" 2>/dev/null; then
  log "ERROR: TARGET_FILE missing markers ($MARKER_START). Add them first."
  exit 1
fi

# --- Collect memory cells ---
log "Querying mem for corrections and lessons..."

CORRECTIONS=$("$MEM_CMD" search-tag correction 2>/dev/null | head -40 || true)
LESSONS=$("$MEM_CMD" scene lessons 2>/dev/null | grep -E "sal:(0\.[89]|1\.0)" | head -30 || true)

COMBINED=$(printf '%s\n%s' "$CORRECTIONS" "$LESSONS" | grep -v '^[[:space:]]*$' || true)

if [[ -z "$COMBINED" ]]; then
  log "No correction or lesson cells found — skipping rule synthesis."
  exit 0
fi

CELL_COUNT=$(echo "$COMBINED" | wc -l)
log "Found $CELL_COUNT cells to synthesize from."

# --- Call LLM to synthesize rules ---
log "Calling LLM to synthesize behavioral rules..."

SYSTEM_PROMPT="You are a rule synthesizer. Given a list of corrections and lessons from past agent sessions, extract terse behavioral rules. Each rule should be one line, imperative mood, specific and actionable. Deduplicate. Output ONLY the rules as a numbered list, max ${MAX_RULES} rules. Prioritize corrections (mistakes to avoid) over general lessons."

USER_PROMPT="Here are the corrections and lessons from past sessions:\n\n${COMBINED}"

# Build JSON payload using Python to safely escape strings
PAYLOAD=$(python3 -c "
import json, sys
system = sys.argv[1]
user = sys.argv[2]
payload = {
    'model': 'openclaw',
    'messages': [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user}
    ]
}
print(json.dumps(payload))
" "$SYSTEM_PROMPT" "$USER_PROMPT")

RESPONSE=$(curl -sf \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$API_URL" 2>&1) || {
    log "ERROR: API call failed: $RESPONSE"
    exit 1
  }

# Extract content from response
RULES=$(python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
choices = data.get('choices', [])
if not choices:
    print('')
else:
    print(choices[0].get('message', {}).get('content', ''))
" <<< "$RESPONSE")

if [[ -z "$RULES" ]]; then
  log "ERROR: LLM returned empty rules. Response: $RESPONSE"
  exit 1
fi

RULE_COUNT=$(echo "$RULES" | grep -c '^[0-9]' || true)
log "Synthesized $RULE_COUNT rules."

# --- Format rules as markdown ---
TIMESTAMP=$(date '+%Y-%m-%d %H:%M %Z')

NEW_BLOCK=$(cat <<BLOCK_EOF
$MARKER_START
## Learned Rules
*Auto-generated $TIMESTAMP from $CELL_COUNT memory cells — do not edit manually*

$RULES
$MARKER_END
BLOCK_EOF
)

# --- Inject into TARGET_FILE ---
python3 - "$TARGET_FILE" "$MARKER_START" "$MARKER_END" "$NEW_BLOCK" <<'PYEOF'
import sys, re
path, start, end, new_block = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
content = open(path).read()
pattern = re.escape(start) + r'.*?' + re.escape(end)
updated = re.sub(pattern, new_block, content, flags=re.DOTALL)
open(path, 'w').write(updated)
PYEOF

log "Injected learned rules into $TARGET_FILE"
log "Done."
