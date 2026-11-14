"""Strip credentials from memory content before it is stored.

Memory cells are copied to replicas, exports and backups, and are injected into
agent prompts, so a token that lands in a cell leaks everywhere at once. Every
write path calls :func:`redact_secrets` (``MemoryDB.insert_cell``) or
:func:`contains_secret` (``archy-reconcile``) so a raw credential never gets
persisted. The fact around the token is kept; only the value is replaced.

Patterns are deliberately high-precision: a missed exotic token format is
cheaper than mangling ordinary prose about tokens ("the token is stored in...").
"""

from __future__ import annotations

import re

# (kind, pattern). When a pattern has a group named "secret", only that group is
# replaced; otherwise the whole match is.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openrouter-key", re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}")),
    ("slack-token", re.compile(r"\bxox[abposre]-[A-Za-z0-9\-]{10,}")),
    ("slack-app-token", re.compile(r"\bxapp-[A-Za-z0-9\-]{10,}")),
    ("slack-webhook", re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/_\-]{20,}")),
    ("discord-webhook", re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("tailscale-key", re.compile(r"\btskey-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+(?P<secret>[A-Za-z0-9._~+/\-]{20,}=*)")),
    ("url-password", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:(?P<secret>[^\s/@]{3,})@")),
    # A password written out as a quoted literal: "password was reset to 'hunter22'",
    # "password: `abc123`". Unquoted prose ("the password is stored in X") is left alone.
    ("password", re.compile(
        r"(?i)\b(?:password|passwd|passphrase|pwd)\b[^.\n'\"`]{0,40}?(?P<secret>(?P<q>['\"`])[^'\"`\s]{4,64}(?P=q))")),
    ("password", re.compile(
        r"(?i)\b(?:password|passwd|passphrase|pwd)\s*[=:]\s*(?P<secret>[^\s'\"`,;]{6,64})")),
]

# Values that look like an assignment but are references, not secrets.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:\$\{?|\{\{|<|\*{3,}|x{4,}|\[REDACTED)|redacted|example|changeme|placeholder|your[_\-]|^env[:.]")


def _replace(kind: str, pattern: re.Pattern[str], text: str, found: list[str]) -> str:
    def sub(m: re.Match[str]) -> str:
        if "secret" in pattern.groupindex and m.group("secret") is not None:
            value = m.group("secret").strip("'\"`")
            if _PLACEHOLDER.search(value):
                return m.group(0)
            found.append(kind)
            start, end = m.span("secret")
            s0 = m.start()
            return m.group(0)[: start - s0] + f"[REDACTED {kind}]" + m.group(0)[end - s0:]
        found.append(kind)
        return f"[REDACTED {kind}]"

    return pattern.sub(sub, text)


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Return ``(clean_text, kinds)``; ``kinds`` lists what was removed (empty if nothing)."""
    if not text:
        return text, []
    found: list[str] = []
    for kind, pattern in _PATTERNS:
        text = _replace(kind, pattern, text, found)
    return text, found


def contains_secret(text: str) -> bool:
    return bool(redact_secrets(text)[1])
