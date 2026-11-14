"""Tests for credential redaction on the memory write path.

Fake tokens are assembled at runtime so secret scanners don't flag this file.
"""

import pytest

from oc_memory.db import MemoryDB
from oc_memory.redact import contains_secret, redact_secrets

SLACK_BOT = "xox" + "b-" + "1234567890-1234567890123-" + "A" * 24
SLACK_APP = "xa" + "pp-1-" + "A0B1C2D3E4F-" + "9" * 13 + "-" + "f" * 64
ANTHROPIC = "sk-" + "ant-api03-" + "Z" * 90
GITHUB = "gh" + "p_" + "a1B2" * 9
AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
TAILSCALE = "ts" + "key-auth-" + "kXyZ123CNTRL-" + "q" * 30


@pytest.mark.parametrize("secret,kind", [
    (SLACK_BOT, "slack-token"),
    (SLACK_APP, "slack-app-token"),
    (ANTHROPIC, "anthropic-key"),
    (GITHUB, "github-token"),
    (AWS, "aws-access-key"),
    (TAILSCALE, "tailscale-key"),
])
def test_token_formats_are_redacted(secret, kind):
    text, kinds = redact_secrets(f"Bot token {secret}, rebuild from the vault.")
    assert secret not in text
    assert kinds == [kind]
    assert text == f"Bot token [REDACTED {kind}], rebuild from the vault."


def test_quoted_password_keeps_the_sentence():
    text, kinds = redact_secrets("Pi-hole admin password was reset to 'hunter2xyz' and the secret updated.")
    assert "hunter2xyz" not in text
    assert text == "Pi-hole admin password was reset to [REDACTED password] and the secret updated."
    assert kinds == ["password"]


def test_password_assignment_and_url_credentials():
    text, _ = redact_secrets("set password=Sup3rS3cret! then connect to postgres://app:pa55word@db:5432/x")
    assert "Sup3rS3cret!" not in text and "pa55word" not in text
    assert "postgres://app:[REDACTED url-password]@db:5432/x" in text


def test_private_key_block():
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----"
    text, kinds = redact_secrets(f"key: {block} done")
    assert "b3BlbnNzaC1rZXk" not in text and kinds == ["private-key"]


@pytest.mark.parametrize("prose", [
    "The gateway token is stored in openclaw.json under gateway.auth.token.",
    "the API password is stored in /etc/pihole/cli_pw inside the pod",
    "Bot token is in ~/.hermes/.env as SLACK_BOT_TOKEN (59-char xoxb- token).",
    "password: ${PIHOLE_PASSWORD}",
    "Key fingerprint: SHA256:abc and ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI public key",
    "pinning to `mcp<1.10` avoids the stdio hang",
    "Already [REDACTED slack-token] earlier",
])
def test_ordinary_prose_is_untouched(prose):
    assert redact_secrets(prose) == (prose, [])
    assert not contains_secret(prose)


def test_insert_cell_never_persists_the_secret(tmp_path):
    db = MemoryDB(tmp_path / "t.db")
    row = db.insert_cell({"scene": "infra", "content": f"Slack bot token {SLACK_BOT}"})
    stored = db.db.execute("select content from mem_cells where id=?", (row,)).fetchone()[0]
    fts = db.db.execute("select content from mem_fts where rowid=?", (row,)).fetchone()[0]
    assert SLACK_BOT not in stored and SLACK_BOT not in fts
    assert stored == "Slack bot token [REDACTED slack-token]"
    assert db.db.execute("select value from mem_stats where key='secrets_redacted'").fetchone()[0] == 1


def test_mcp_store_redacts(tmp_path, monkeypatch):
    from oc_memory import mcp_server
    monkeypatch.setattr(mcp_server, "_db", MemoryDB(tmp_path / "m.db"))

    class NoEmbed:
        def is_available(self):
            return False
    monkeypatch.setattr(mcp_server, "_embedder", NoEmbed())
    mcp_server.tool_memory_store({"content": f"app token {SLACK_APP}", "scene": "slack"})
    content = mcp_server._db.db.execute("select content from mem_cells").fetchone()[0]
    assert content == "app token [REDACTED slack-app-token]"
