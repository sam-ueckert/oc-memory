---
name: recall
description: Search and retrieve memories from oc-memory (SQLite/FTS5) and markdown memory files. Use when the user asks about prior work, decisions, past conversations, people, preferences, dates, todos, or any question that requires recalling stored context. Also triggers on single-word or short-phrase lookups that imply "what do you know about X."
---

# Recall

Multi-layer memory retrieval: structured DB first, markdown fallback, grep sweep.

## Workflow

1. **Structured search** — fast, ranked, typed cells:
   ```bash
   mem search "<query>"
   ```
   Returns cell ID, type, scene, salience, and content snippet.

2. **Markdown memory search** — if the platform provides `memory_search`, use it:
   ```
   memory_search(query="<query>")
   ```
   Then `memory_get(path, from, lines)` to pull relevant snippets.

3. **Grep fallback** — catch anything the other layers missed:
   ```bash
   grep -ri "<keyword>" memory/ MEMORY.md
   ```

4. **Synthesize** — combine results across layers. Cite sources when helpful:
   - Structured: `[cell #ID, scene:<scene>]`
   - Markdown: `Source: <path>#<line>`

## Guidelines

- Run **all applicable layers** (don't stop at the first hit — cross-reference).
- For ambiguous single-word queries, search broadly then ask for clarification if results are thin.
- If nothing is found across all layers, say so clearly.
- When results exist but are sparse, mention what you found and offer to dig deeper.
- Never fabricate memories. Only report what's actually stored.

## Storing New Memories

If the recall process surfaces a gap worth filling, offer to store it:
```bash
mem quick-store <scene> <type> <salience> "<content>"
```

Cell types: `fact`, `decision`, `preference`, `task`, `risk`, `plan`, `lesson`
Salience: 0.1 (trivia) → 0.5 (normal) → 0.8 (important) → 1.0 (critical)
