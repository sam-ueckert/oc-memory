# OpenClaw Plugins for oc-memory

This directory holds oc-memory integrations that use OpenClaw's supported
*plugin* seam (`api.on("before_prompt_build", ...)` and related hooks
documented in OpenClaw's `docs/plugins/hooks.md`), as opposed to the
internal/shell hooks in [`../hooks`](../hooks) (`message:received` /
`message:sent`).

## oc-memory-recall

Same-turn recall via `before_prompt_build`. See
[`oc-memory-recall/PLUGIN.md`](oc-memory-recall/PLUGIN.md).

Recall-only — no capture/write behavior. For automatic capture, see the
separate [`hooks/oc-memory-capture`](../hooks/oc-memory-capture) hook, which
uses `message:sent` (capture does not need same-turn timing, so the
fire-and-forget hook path is sufficient for it).
