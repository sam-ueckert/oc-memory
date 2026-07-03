#!/usr/bin/env python3
"""
oc-memory TUI installer

Usage:
    python3 install.py [--yes] [--local | --docker | --local-replica]

Flags:
    --yes, -y          Non-interactive: accept all defaults
    --local            Skip mode selection, force local Python install
    --docker           Skip mode selection, force Docker install
    --local-replica    Skip mode selection, force Litestream local-replica setup
                        (see docs/local-replica.md)

Environment overrides:
    NONINTERACTIVE=1            Same as --yes
    INSTALL_MODE=local          Same as --local
    INSTALL_MODE=docker         Same as --docker
    INSTALL_MODE=local-replica  Same as --local-replica
    OC_MEMORY_HOME              Override data directory (local mode)
    OC_MEMORY_SSL_NO_VERIFY=1   Skip TLS cert verification (corporate proxies)
    REQUESTS_CA_BUNDLE          Path to custom CA bundle
    OC_MEMORY_OC_CONFIG         Path to openclaw.json
    RSYNC_SOURCE                user@host:/path/ to the primary's litestream replica (local-replica mode)
    OC_MEMORY_REMOTE_URL        Canonical remote server URL for writes (local-replica mode)
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ── Terminal colors (ANSI) ────────────────────────────────────────────────────

RED    = "\033[0;31m"
GREEN  = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE   = "\033[0;34m"
CYAN   = "\033[0;36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
NC     = "\033[0m"

def ok(msg):    print(f"  {GREEN}✓{NC} {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{NC} {msg}")
def err(msg):   print(f"  {RED}✗{NC} {msg}")
def info(msg):  print(f"  {BLUE}→{NC} {msg}")
def hdr(title): print(f"\n{BOLD}  ── {title} {'─' * max(0, 46 - len(title))}{NC}\n")
def nl():       print()

# ── Non-interactive / mode flags ──────────────────────────────────────────────

YES          = "--yes" in sys.argv or "-y" in sys.argv or os.environ.get("NONINTERACTIVE") == "1"
_mode_flag   = ("docker" if "--docker" in sys.argv else
                "local-replica" if "--local-replica" in sys.argv else
                "local" if "--local" in sys.argv else "")
INSTALL_MODE = _mode_flag or os.environ.get("INSTALL_MODE", "")
SCRIPT_DIR   = Path(__file__).parent.resolve()

# ── UI primitives ─────────────────────────────────────────────────────────────

def ask(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt. Auto-returns default in --yes mode."""
    if YES:
        label = "y" if default else "n"
        print(f"  {DIM}{prompt} [auto: {label}]{NC}")
        return default
    marker = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{marker}]: ").strip().lower()
    return raw in ("y", "yes") if raw else default


def prompt_text(prompt: str, default: str = "") -> str:
    """Free-text prompt. Auto-returns default in --yes mode."""
    if YES:
        print(f"  {DIM}{prompt} [auto: {default!r}]{NC}")
        return default
    raw = input(f"  {prompt} [{default}]: ").strip()
    return raw if raw else default


def menu(title: str, options: list[tuple[str, str]], default: int = 0) -> int:
    """Numbered single-select menu. Returns 0-based index."""
    print(f"\n{BOLD}  {title}{NC}\n")
    for i, (label, desc) in enumerate(options):
        dot = f"{GREEN}●{NC}" if i == default else f"{DIM}○{NC}"
        rec = f"  {CYAN}(recommended){NC}" if i == default else ""
        print(f"    {dot} {BOLD}{i + 1}.{NC} {BOLD}{label}{NC}{rec}")
        if desc:
            print(f"         {DIM}{desc}{NC}")
    nl()
    if YES:
        print(f"  {DIM}[auto: {default + 1}]{NC}")
        return default
    while True:
        raw = input(f"  Select [1-{len(options)}] (default {default + 1}): ").strip()
        if not raw:
            return default
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass


def checklist(title: str, items: list[tuple[str, str, str]], defaults: list[int] | None = None) -> list[str]:
    """Toggle-list multi-select.

    items  = [(key, label, description), ...]
    defaults = list of indices to pre-select (default: all)
    Returns list of selected keys.
    """
    selected: set[int] = set(defaults if defaults is not None else range(len(items)))
    print(f"\n{BOLD}  {title}{NC}")
    print(f"  {DIM}Enter a number to toggle, or press Enter to confirm.{NC}\n")

    while True:
        for i, (_, label, desc) in enumerate(items):
            mark = f"{GREEN}✓{NC}" if i in selected else " "
            print(f"    [{mark}] {BOLD}{i + 1}.{NC} {BOLD}{label}{NC} — {DIM}{desc}{NC}")
        nl()
        if YES:
            print(f"  {DIM}[auto: confirming selections]{NC}")
            break
        raw = input("  Toggle number, or Enter to confirm: ").strip()
        if not raw:
            break
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                selected.discard(idx) if idx in selected else selected.add(idx)
                # redraw
                print(f"\033[{len(items) + 2}A", end="")  # move cursor up
                print("\033[J", end="")                    # clear to end of screen
        except ValueError:
            pass

    return [items[i][0] for i in sorted(selected)]


# ── Header ────────────────────────────────────────────────────────────────────

def print_header() -> None:
    print()
    print(f"{CYAN}  ╔══════════════════════════════════════════════════╗{NC}")
    print(f"{CYAN}  ║{NC}{BOLD}          oc-memory v2 — MCP memory server          {NC}{CYAN}║{NC}")
    print(f"{CYAN}  ╚══════════════════════════════════════════════════╝{NC}")
    print()
    print(f"  Persistent memory for {BOLD}Claude Code{NC}, {BOLD}Cursor{NC}, and {BOLD}OpenClaw{NC}.")
    print(f"  {DIM}SQLite + FTS5 + ONNX embeddings · 12 MCP tools · zero external deps{NC}")
    print()


# ── Docker helpers ────────────────────────────────────────────────────────────

def find_compose() -> tuple[str, list[str]] | None:
    """Return (binary, subcommand_list) for docker compose, or None."""
    if shutil.which("docker"):
        r = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if r.returncode == 0:
            return ("docker", ["compose"])
    if shutil.which("docker-compose"):
        return ("docker-compose", [])
    return None


def wait_for_url(url: str, timeout: int = 60, interval: int = 2) -> bool:
    """Poll url until it returns HTTP 2xx. Returns True on success."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 300:
                    print()
                    return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(interval)
    print()
    return False


def write_compose_override(port: int) -> None:
    """Write docker-compose.override.yml if port differs from default 8765."""
    if port == 8765:
        return
    override = (
        "services:\n"
        "  oc-memory:\n"
        f"    ports:\n      - \"{port}:{port}\"\n"
        f"    environment:\n      - MCP_PORT={port}\n"
    )
    override_path = SCRIPT_DIR / "docker-compose.override.yml"
    override_path.write_text(override)
    ok(f"Wrote docker-compose.override.yml (port {port})")


# ── Docker install ────────────────────────────────────────────────────────────

def do_docker() -> int:
    """Run Docker install flow. Returns the port the server is listening on."""
    hdr("Docker Setup")

    compose = find_compose()
    if not compose:
        err("Docker not found. Install Docker Desktop: https://docs.docker.com/get-docker/")
        sys.exit(1)
    ok(f"Docker found ({compose[0]})")

    port = int(prompt_text("MCP server port", "8765") or "8765")
    write_compose_override(port)

    nl()
    info("Starting oc-memory container (first build may take a minute)…")
    binary, sub = compose
    cmd = [binary] + sub + ["-f", str(SCRIPT_DIR / "docker-compose.yml"), "up", "-d", "--build"]
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    if result.returncode != 0:
        err("docker compose up failed — check Docker logs.")
        sys.exit(1)

    health_url = f"http://localhost:{port}/health"
    print(f"  Waiting for server at {health_url}", end="", flush=True)
    if wait_for_url(health_url, timeout=90):
        ok(f"Server ready at http://localhost:{port}/mcp")
    else:
        warn(f"Server did not respond within 90s — check: docker logs oc-memory")

    return port


# ── Local Python install ───────────────────────────────────────────────────────

def do_local() -> None:
    """Run local Python install flow."""
    hdr("Local Python Setup")

    if sys.version_info < (3, 11):
        err(f"Python 3.11+ required (found {sys.version_info.major}.{sys.version_info.minor})")
        sys.exit(1)
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    data_dir = Path(os.environ.get("OC_MEMORY_HOME", Path.home() / ".oc-memory"))
    data_dir.mkdir(parents=True, exist_ok=True)
    ok(f"Data directory: {data_dir}")

    nl()
    info("Installing oc-memory…")
    if shutil.which("uv"):
        subprocess.run(["uv", "pip", "install", "-e", str(SCRIPT_DIR)], check=True)
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(SCRIPT_DIR)], check=True)
    ok("oc-memory installed")

    nl()
    info("Downloading ONNX embedding model (bge-small-en-v1.5, ~24 MB)…")
    result = subprocess.run(
        [sys.executable, "-c",
         "from oc_memory.embedding_backends import download_onnx_model, is_model_downloaded\n"
         "if is_model_downloaded(): print('cached')\n"
         "else: download_onnx_model(); print('downloaded')"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        label = "already cached" if result.stdout.strip() == "cached" else "ready"
        ok(f"Embedding model {label}")
    else:
        warn("ONNX model download failed — proceeding in FTS-only mode.")
        info("To fix on a corporate network:")
        info("  export OC_MEMORY_SSL_NO_VERIFY=1")
        info("  export REQUESTS_CA_BUNDLE=/path/to/corp-ca.crt")
        info("Retry later: oc-memory embed  (after the model is cached)")


# ── MCP client config patching ────────────────────────────────────────────────

def _http_entry(port: int) -> dict:
    return {"type": "http", "url": f"http://localhost:{port}/mcp"}


def _stdio_entry() -> dict:
    """Generate stdio entry using installed oc-memory-mcp entry point."""
    cmd = shutil.which("oc-memory-mcp") or "oc-memory-mcp"
    return {"command": cmd, "args": []}


def patch_claude_json(entry: dict) -> None:
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        claude_json.write_text(json.dumps({"mcpServers": {}}, indent=2))
        info("Created ~/.claude.json")
    config = json.loads(claude_json.read_text())
    config.setdefault("mcpServers", {})
    if "oc-memory" in config["mcpServers"]:
        ok("oc-memory already in ~/.claude.json — skipping")
    else:
        config["mcpServers"]["oc-memory"] = entry
        claude_json.write_text(json.dumps(config, indent=2))
        ok("Patched ~/.claude.json")
        info("Restart Claude Code to activate")


def patch_cursor_json(entry: dict) -> None:
    cursor_json = Path.home() / ".cursor" / "mcp.json"
    cursor_json.parent.mkdir(parents=True, exist_ok=True)
    config = json.loads(cursor_json.read_text()) if cursor_json.exists() else {}
    config.setdefault("mcpServers", {})
    if "oc-memory" in config["mcpServers"]:
        ok("oc-memory already in ~/.cursor/mcp.json — skipping")
    else:
        config["mcpServers"]["oc-memory"] = entry
        cursor_json.write_text(json.dumps(config, indent=2))
        ok("Patched ~/.cursor/mcp.json")
        info("Restart Cursor to activate")


def patch_openclaw_json(entry: dict) -> None:
    oc_json_path = os.environ.get("OC_MEMORY_OC_CONFIG", str(Path.home() / ".openclaw" / "openclaw.json"))
    oc_json = Path(oc_json_path)
    if not oc_json.exists():
        warn(f"openclaw.json not found at {oc_json} — skipping")
        return
    config = json.loads(oc_json.read_text())
    config.setdefault("mcp", {}).setdefault("servers", [])
    existing_names = [s.get("name") for s in config["mcp"]["servers"]]
    if "oc-memory" in existing_names:
        ok("oc-memory already in openclaw.json — skipping")
    else:
        server_entry = {"name": "oc-memory", "transport": "stdio", **entry} if "command" in entry else {
            "name": "oc-memory", "transport": "http", "url": entry["url"]
        }
        config["mcp"]["servers"].append(server_entry)
        oc_json.write_text(json.dumps(config, indent=2))
        ok(f"Patched {oc_json}")
        info("Restart OpenClaw to activate")


def do_client_config(mcp_entry: dict) -> None:
    hdr("MCP Client Configuration")

    # Detect what's present
    has_claude  = (Path.home() / ".claude.json").exists()
    has_cursor  = (Path.home() / ".cursor").exists()
    oc_path     = os.environ.get("OC_MEMORY_OC_CONFIG", str(Path.home() / ".openclaw" / "openclaw.json"))
    has_openclaw = Path(oc_path).exists()

    items = [
        ("claude",   "Claude Code",
         f"{'detected' if has_claude else 'will create'} ~/.claude.json"),
        ("cursor",   "Cursor",
         f"{'detected' if has_cursor else 'not detected — will create'} ~/.cursor/mcp.json"),
        ("openclaw", "OpenClaw",
         f"{'detected' if has_openclaw else f'not found at {oc_path}'} — skip if not using"),
    ]
    # Pre-select detected clients; always pre-select Claude
    defaults = [i for i, (k, _, _) in enumerate(items) if k == "claude" or
                (k == "cursor" and has_cursor) or (k == "openclaw" and has_openclaw)]

    selected = checklist("Which AI clients should oc-memory connect to?", items, defaults)

    nl()
    if "claude" in selected:
        patch_claude_json(mcp_entry)
    if "cursor" in selected:
        patch_cursor_json(mcp_entry)
    if "openclaw" in selected:
        patch_openclaw_json(mcp_entry)


# ── mem CLI ────────────────────────────────────────────────────────────────────

def install_mem_cli(docker_port: int | None) -> None:
    mem_cli_src = SCRIPT_DIR / "cli" / "mem"
    if not mem_cli_src.exists():
        warn("cli/mem not found — skipping mem CLI install")
        return

    bin_dir = Path.home() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    dest = bin_dir / "mem"
    shutil.copy(mem_cli_src, dest)
    dest.chmod(0o755)
    ok(f"Installed mem CLI to ~/bin/mem")

    if Path("/usr/local/bin").exists() and shutil.which("mem") is None:
        if str(bin_dir) not in os.environ.get("PATH", ""):
            warn("~/bin is not in your PATH. Add it:")
            shell_rc = ".zshrc" if os.environ.get("SHELL", "").endswith("zsh") else ".bashrc"
            info(f'  echo \'export PATH="$HOME/bin:$PATH"\' >> ~/{shell_rc}')
            info(f"  source ~/{shell_rc}")

    nl()
    if docker_port:
        info(f"mem CLI points at Docker server (http://localhost:{docker_port})")
        info("  mem stats")
        info("  mem search 'first memory'")
        if docker_port != 8765:
            info(f'  export MEM_MCP_URL="http://localhost:{docker_port}"   # add to shell rc')
    else:
        info("mem CLI is in local mode by default (no server needed):")
        info("  export MEM_LOCAL=1")
        info("  mem stats")
        info("  mem store myproject fact 0.8 'My first memory'")


# ── Context digest ─────────────────────────────────────────────────────────────

def install_context_digest() -> None:
    src = SCRIPT_DIR / "skills" / "context-digest" / "scripts" / "gen-context-digest.sh"
    if not src.exists():
        warn("gen-context-digest.sh not found — skipping")
        return
    dest = Path.home() / "bin" / "gen-context-digest.sh"
    shutil.copy(src, dest)
    dest.chmod(0o755)
    ok("Installed gen-context-digest.sh to ~/bin/")
    info("Add markers to your CLAUDE.md or MEMORY.md:")
    info("  <!-- ARCHY_DIGEST_START -->")
    info("  <!-- ARCHY_DIGEST_END -->")
    info("Test: WORKSPACE=$(pwd) bash ~/bin/gen-context-digest.sh")


# ── Promote lessons ────────────────────────────────────────────────────────────

def install_promote_lessons() -> None:
    src = SCRIPT_DIR / "skills" / "promote-lessons" / "scripts" / "promote-lessons.sh"
    if not src.exists():
        warn("promote-lessons.sh not found — skipping")
        return
    dest = Path.home() / "bin" / "promote-lessons.sh"
    shutil.copy(src, dest)
    dest.chmod(0o755)
    ok("Installed promote-lessons.sh to ~/bin/")
    info("Add markers to your SOUL.md or CLAUDE.md:")
    info("  <!-- LEARNED_RULES_START -->")
    info("  ## Learned Rules")
    info("  *No rules yet.*")
    info("  <!-- LEARNED_RULES_END -->")
    info("Run:")
    info("  Claude Code: API_TOKEN=$ANTHROPIC_API_KEY WORKSPACE=$(pwd) bash ~/bin/promote-lessons.sh")
    info("  OpenClaw:    API_TOKEN=<gateway-token> WORKSPACE=$(pwd) bash ~/bin/promote-lessons.sh")


# ── Multi-user ────────────────────────────────────────────────────────────────

def setup_multi_user() -> None:
    shell_rc = ".zshrc" if os.environ.get("SHELL", "").endswith("zsh") else ".bashrc"
    info("Add to your shell rc:")
    info(f'  export OC_MEMORY_ADMIN_USER="<your-user-id>"   # add to ~/{shell_rc}')
    db_path = Path(os.environ.get("OC_MEMORY_DB",
                                  Path.home() / ".oc-memory" / "memory.db"))
    if db_path.exists() and not YES:
        nl()
        if ask("Backfill owner_id on existing database?", default=False):
            admin_id = input("  Admin user ID: ").strip()
            if admin_id:
                migrate = SCRIPT_DIR / "scripts" / "migrate-ownership.py"
                subprocess.run([sys.executable, str(migrate),
                                "--admin-user", admin_id, "--db", str(db_path)])
                ok(f"Migration complete")
            else:
                info("Skipping — no user ID provided")


# ── Google Drive ───────────────────────────────────────────────────────────────

def setup_drive() -> None:
    info("Installing Drive dependencies…")
    if shutil.which("uv"):
        subprocess.run(["uv", "pip", "install", "-e", f"{SCRIPT_DIR}[drive]"], check=True)
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", f"{SCRIPT_DIR}[drive]"], check=True)
    ok("Drive dependencies installed")
    info("Set up OAuth2 credentials:")
    info("  1. https://console.cloud.google.com/apis/credentials")
    info("  2. Create OAuth 2.0 Client ID → Desktop app")
    info("  3. Save JSON to ~/.oc-memory/drive-client-creds.json")
    info("  First run: oc-memory backup-drive  (opens browser for auth)")


# ── Cron jobs ─────────────────────────────────────────────────────────────────

def _crontab_has(entry: str) -> bool:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return entry in (r.stdout or "")


def _add_cron(label: str, entry: str) -> None:
    if _crontab_has(entry):
        ok(f"Already installed: {label}")
        return
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = r.stdout if r.returncode == 0 else ""
    new_crontab = existing.rstrip("\n") + "\n" + entry + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
    if proc.returncode == 0:
        ok(f"Installed cron: {label}")
    else:
        warn(f"Failed to install cron: {label} — {proc.stderr.strip()}")


def setup_crons(features: list[str], workspace: str, docker_port: int | None, api_token: str) -> None:
    if platform.system() == "Darwin" or shutil.which("crontab"):
        pass
    else:
        warn("crontab not found — skipping cron setup")
        return

    mem_prefix = f"MEM_MCP_URL=http://localhost:{docker_port} " if docker_port else "MEM_LOCAL=1 "
    mem_cmd    = f"{Path.home()}/bin/mem"
    oc_cmd     = "oc-memory"

    if "digest" in features:
        digest_script = f"{Path.home()}/bin/gen-context-digest.sh"
        if Path(digest_script).exists():
            entry = f"0 */3 * * * OC_MEMORY_WORKSPACE=\"{workspace}\" bash {digest_script} >> /tmp/oc-memory-digest.log 2>&1"
            _add_cron("context-digest (every 3h)", entry)
        else:
            warn("gen-context-digest.sh not installed — skipping digest cron")

    if "embed" in features:
        # Memory extraction: embed new cells + consolidate scenes
        entry = f"*/30 * * * * {mem_prefix}{oc_cmd} embed >> /tmp/oc-memory-embed.log 2>&1"
        _add_cron("memory embed (every 30min)", entry)
        entry2 = f"0 2 * * * {oc_cmd} consolidate >> /tmp/oc-memory-consolidate.log 2>&1"
        _add_cron("memory consolidate (daily 2am)", entry2)

    if "prune" in features:
        entry = f"0 3 * * * {oc_cmd} decay >> /tmp/oc-memory-decay.log 2>&1"
        _add_cron("memory decay (daily 3am)", entry)

    if "promote" in features:
        lessons_script = f"{Path.home()}/bin/promote-lessons.sh"
        if Path(lessons_script).exists() and api_token:
            entry = (
                f'0 3 * * 0 API_TOKEN="{api_token}" '
                f'OC_MEMORY_WORKSPACE="{workspace}" '
                f"bash {lessons_script} >> /tmp/oc-memory-lessons.log 2>&1"
            )
            _add_cron("promote-lessons (Sunday 3am)", entry)
        elif not api_token:
            warn("No API token — skipping promote-lessons cron (set API_TOKEN manually)")
        else:
            warn("promote-lessons.sh not installed — skipping cron")

    # Show installed entries
    nl()
    info("Active oc-memory crons:")
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [ln for ln in (r.stdout or "").splitlines()
             if any(k in ln for k in ("oc-memory", "gen-context-digest", "promote-lessons"))]
    for ln in lines:
        print(f"    {DIM}{ln}{NC}")
    if not lines:
        print(f"    {DIM}(none){NC}")


# ── Hermes session extractor ─────────────────────────────────────────────────

def _default_mcp_url(docker_port: int | None) -> str:
    if docker_port:
        return f"http://localhost:{docker_port}/mcp"
    return os.environ.get("OC_MEMORY_MCP_URL", "http://localhost:8765/mcp")


def _hermes_env_path() -> Path:
    return Path(os.environ.get("OC_MEMORY_HOME", Path.home() / ".oc-memory")) / "hermes.env"


def _read_hermes_env() -> dict:
    """Parse an existing hermes.env into a dict (empty if absent)."""
    path = _hermes_env_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def setup_hermes_extractor(docker_port: int | None) -> None:
    """Configure the Hermes → archy session memory extractor + sink mode.

    Reconfigure-safe: when an existing hermes.env is present, its values are
    used as defaults so a second run edits the current setup instead of
    resetting it.

    When MCP-only is chosen and a local memory DB still holds cells, offer to
    migrate them to the MCP server first. Migration is a non-destructive copy
    (local DB is only read), and we only commit to MCP-only once every cell has
    landed on the server — otherwise we keep the local copy as a safety net.
    """
    info("Extracts Hermes agent sessions and pushes distilled memories to archy.")

    existing = _read_hermes_env()
    if existing:
        nl()
        info("Existing extractor config found — reconfiguring (current values are the defaults).")
    nl()

    prev_sink = existing.get("OC_MEMORY_HERMES_SINK", "mcp")
    sink_idx = menu(
        "Where should extracted memories be stored?",
        [
            ("MCP only",      "Push only to the remote archy MCP server (central source of truth)"),
            ("MCP + local",   "Push to MCP and keep a local SQLite copy as cache/fallback"),
        ],
        default=0 if prev_sink == "mcp" else 1,
    )
    sink = "mcp" if sink_idx == 0 else "both"

    mcp_url = prompt_text("archy MCP server URL",
                          existing.get("OC_MEMORY_MCP_URL") or _default_mcp_url(docker_port))
    source  = prompt_text("Filter Hermes sessions by source (blank = all)",
                          existing.get("OC_MEMORY_HERMES_SOURCE", ""))

    migrated_marker = existing.get("OC_MEMORY_MCP_MIGRATED", "")

    # Detect an existing local memory DB.
    local_db = Path(os.environ.get("OC_MEMORY_DB", Path.home() / ".oc-memory" / "memory.db"))
    local_count = _count_local_cells(local_db)

    if sink == "mcp" and local_count > 0:
        nl()
        warn(f"Found an existing local memory DB with {local_count} cells: {local_db}")
        info("MCP-only mode won't read this local DB at extraction time.")
        info("(Migration copies cells to the server; the local DB is left untouched.)")

        already = migrated_marker == mcp_url
        if already:
            info(f"These memories were already migrated to {mcp_url} on a previous run.")
            prompt_msg = "Migrate again? (may create duplicates on the server)"
        else:
            prompt_msg = f"Migrate these {local_count} memories to the MCP server now?"

        if ask(prompt_msg, default=not already):
            success = _run_migration(str(local_db), mcp_url)
            if success:
                migrated_marker = mcp_url
            else:
                nl()
                warn("Migration did not fully succeed — your local memories are still intact.")
                info("Switching to MCP-only now would orphan the un-migrated cells.")
                if ask("Keep a local copy (use MCP + local) until migration succeeds?", default=True):
                    sink = "both"
                    ok("Sink set to MCP + local — local data is preserved as a safety net.")
                else:
                    warn("Proceeding with MCP-only. Re-run migration manually before relying on it:")
                    info(f"  oc-memory migrate-to-mcp --db {local_db} --mcp-url {mcp_url}")
        elif not already:
            nl()
            info("Skipping migration. Keeping MCP + local so local memories aren't orphaned.")
            sink = "both"
    elif sink == "mcp":
        ok("No existing local memory DB found — nothing to migrate.")

    _write_hermes_env(sink, mcp_url, source, migrated=migrated_marker)


def _count_local_cells(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM mem_cells").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def _run_migration(db_path: str, mcp_url: str) -> bool:
    """Run the migrate-to-mcp command. Returns True only on full success."""
    info("Migrating local memories to the MCP server…")
    cmd = [sys.executable, "-m", "oc_memory.cli", "migrate-to-mcp",
           "--db", db_path, "--mcp-url", mcp_url]
    r = subprocess.run(cmd)
    if r.returncode == 0:
        ok("Migration complete — all local cells are now on the MCP server.")
        return True
    warn("Migration incomplete (server unreachable or some cells rejected).")
    info("Run manually once the server is reachable:")
    info(f"  oc-memory migrate-to-mcp --db {db_path} --mcp-url {mcp_url}")
    return False


def _write_hermes_env(sink: str, mcp_url: str, source: str, migrated: str = "") -> None:
    """Persist extractor defaults to ~/.oc-memory/hermes.env for cron/manual use."""
    env_path = _hermes_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"OC_MEMORY_HERMES_SINK={sink}",
        f"OC_MEMORY_MCP_URL={mcp_url}",
    ]
    if source:
        lines.append(f"OC_MEMORY_HERMES_SOURCE={source}")
    if migrated:
        # Records the URL local cells were already migrated to, so a re-run
        # won't blindly re-push and create duplicates.
        lines.append(f"OC_MEMORY_MCP_MIGRATED={migrated}")
    env_path.write_text("\n".join(lines) + "\n")
    ok(f"Wrote extractor config: {env_path}")
    nl()
    src_arg = f" --source {source}" if source else ""
    info("Run the extractor (where Hermes lives):")
    info(f"  oc-memory extract-hermes --sink {sink} --mcp-url {mcp_url}{src_arg}")


# ── Optional features ──────────────────────────────────────────────────────────

def do_optional_features(docker_port: int | None) -> None:
    hdr("Optional Features")

    items = [
        ("mem_cli",    "mem CLI",          "Quick memory commands in your terminal"),
        ("digest",     "Context digest",   "Prime agent context with top memories at session start"),
        ("promote",    "Promote lessons",  "Turn tagged corrections into standing behavioral rules"),
        ("hermes",     "Hermes extractor", "Extract Hermes agent sessions → push memories to archy (MCP)"),
        ("multi_user", "Multi-user",       "Per-user ownership/visibility (shared server setups)"),
        ("drive",      "Google Drive",     "Back up memory.db + exports to Google Drive"),
        ("crons",      "Cron jobs",        "Schedule embedding, pruning, digest, and lesson promotion"),
    ]
    # Default: first three on, rest off
    selected = checklist("Select features to install", items, defaults=[0, 1, 2])

    if "mem_cli" in selected:
        nl(); hdr("mem CLI")
        install_mem_cli(docker_port)

    if "digest" in selected:
        nl(); hdr("Context Digest")
        install_context_digest()

    if "promote" in selected:
        nl(); hdr("Promote Lessons")
        install_promote_lessons()

    if "hermes" in selected:
        nl(); hdr("Hermes Session Extractor")
        setup_hermes_extractor(docker_port)

    if "multi_user" in selected:
        nl(); hdr("Multi-User Isolation")
        setup_multi_user()

    if "drive" in selected:
        nl(); hdr("Google Drive Backup")
        setup_drive()

    if "crons" in selected:
        nl(); hdr("Cron Jobs")
        workspace = prompt_text("Workspace path for cron jobs", str(Path.cwd()))

        cron_items = [
            ("digest",  "Context digest",     "Refresh CLAUDE.md/MEMORY.md digest every 3h"),
            ("embed",   "Memory extraction",  "Embed new cells (30min) + consolidate scenes (daily 2am)"),
            ("prune",   "Memory decay",       "Fade old low-access cells (daily 3am)"),
            ("promote", "Promote lessons",    "Synthesize correction rules (Sunday 3am, needs API token)"),
        ]
        cron_defaults = [i for i, (k, _, _) in enumerate(cron_items)
                         if k in ("digest", "embed", "prune")]
        cron_selected = checklist("Which cron jobs to install?", cron_items, cron_defaults)

        api_token = ""
        if "promote" in cron_selected:
            api_token = prompt_text(
                "API token for rule synthesis (Anthropic key or OpenClaw gateway token)",
                os.environ.get("ANTHROPIC_API_KEY", "")
            )

        setup_crons(cron_selected, workspace, docker_port, api_token)


# ── Local replica mode (Litestream) ────────────────────────────────────────────

def _launchd_plist(label: str, program_args: list[str], env: dict[str, str], log_dir: Path) -> str:
    args_xml = "".join(f"<string>{a}</string>" for a in program_args)
    env_xml = "".join(f"<key>{k}</key><string>{v}</string>" for k, v in env.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key><array>{args_xml}</array>
    <key>EnvironmentVariables</key><dict>{env_xml}</dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log_dir}/{label}.out.log</string>
    <key>StandardErrorPath</key><string>{log_dir}/{label}.err.log</string>
</dict>
</plist>
"""


def do_local_replica() -> int:
    """Set up Litestream follow + the local hybrid MCP server. Returns the local port."""
    hdr("Local Replica Setup (Litestream)")

    if not shutil.which("litestream"):
        if platform.system() == "Darwin":
            warn("litestream not found. Install it with:")
            info("  brew install benbjohnson/litestream/litestream")
        else:
            warn("litestream not found. Download a release for your platform:")
            info("  https://litestream.io/install/linux/")
        sys.exit(1)
    ok(f"litestream found ({shutil.which('litestream')})")

    rsync_source = prompt_text(
        "rsync source for the primary's litestream-replica dir (user@host:/path/, trailing slash matters)",
        os.environ.get("RSYNC_SOURCE", ""),
    )
    if not rsync_source:
        err("An rsync source is required (e.g. user@host:/path/to/litestream-replica/)")
        info("Note: Litestream's own sftp replica type issues a round trip per file/stat/read, "
             "which can hang against a slow or resource-constrained primary — rsync mirrors the "
             "directory locally first, then Litestream restores from that local copy (no network).")
        sys.exit(1)

    remote_url = prompt_text(
        "Canonical remote server URL (Streamable HTTP, for writes)",
        os.environ.get("OC_MEMORY_REMOTE_URL", ""),
    )
    if not remote_url:
        err("A remote URL is required (e.g. http://primary-host:8765/mcp)")
        sys.exit(1)

    local_db = str(Path.home() / ".oc-memory" / "local-replica" / "memory.db")
    mirror_dir = str(Path.home() / ".oc-memory" / "litestream-mirror")
    port = int(prompt_text("Local hybrid server port", "8765") or "8765")

    log_dir = Path.home() / ".oc-memory" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    nl()
    info("Installing oc-memory (local hybrid server)…")
    if shutil.which("uv"):
        subprocess.run(["uv", "pip", "install", "-e", str(SCRIPT_DIR)], check=True)
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(SCRIPT_DIR)], check=True)
    ok("oc-memory installed")

    if platform.system() == "Darwin":
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        rsync_plist = agents_dir / "com.oc-memory.rsync-mirror.plist"
        rsync_plist.write_text(_launchd_plist(
            "com.oc-memory.rsync-mirror",
            [str(SCRIPT_DIR / "scripts" / "rsync-mirror-loop.sh")],
            {"RSYNC_SOURCE": rsync_source, "MIRROR_DIR": mirror_dir},
            log_dir,
        ))
        subprocess.run(["launchctl", "load", str(rsync_plist)], capture_output=True)
        ok(f"launchd: rsync mirror → {mirror_dir}")

        follow_plist = agents_dir / "com.oc-memory.litestream-follow.plist"
        follow_plist.write_text(_launchd_plist(
            "com.oc-memory.litestream-follow",
            [str(SCRIPT_DIR / "scripts" / "litestream-follow.sh")],
            {"MIRROR_DIR": mirror_dir, "LOCAL_DB": local_db},
            log_dir,
        ))
        subprocess.run(["launchctl", "load", str(follow_plist)], capture_output=True)
        ok(f"launchd: litestream follow (local, no network) → {local_db}")

        server_plist = agents_dir / "com.oc-memory.local-replica-server.plist"
        server_plist.write_text(_launchd_plist(
            "com.oc-memory.local-replica-server",
            [shutil.which("oc-memory-local") or "oc-memory-local", "--http"],
            {
                "OC_MEMORY_LOCAL_DB": local_db,
                "OC_MEMORY_REMOTE_URL": remote_url,
                "MCP_TRANSPORT": "http",
                "MCP_PORT": str(port),
            },
            log_dir,
        ))
        subprocess.run(["launchctl", "load", str(server_plist)], capture_output=True)
        ok(f"launchd: local hybrid server → http://localhost:{port}/mcp")
    else:
        warn("Automatic service setup is macOS-only (launchd). On Linux, run these as systemd "
             "user units or under your process supervisor of choice:")
        info(f"  RSYNC_SOURCE={rsync_source} MIRROR_DIR={mirror_dir} "
             f"{SCRIPT_DIR / 'scripts' / 'rsync-mirror-loop.sh'}")
        info(f"  MIRROR_DIR={mirror_dir} LOCAL_DB={local_db} "
             f"{SCRIPT_DIR / 'scripts' / 'litestream-follow.sh'}")
        info(f"  OC_MEMORY_LOCAL_DB={local_db} OC_MEMORY_REMOTE_URL={remote_url} "
             f"MCP_TRANSPORT=http MCP_PORT={port} oc-memory-local --http")

    return port


# ── Summary ────────────────────────────────────────────────────────────────────

def print_summary(mode: str, port: int | None) -> None:
    hdr("Setup Complete")
    nl()

    if mode == "docker":
        ok(f"MCP server: http://localhost:{port}/mcp")
        nl()
        print(f"  {BOLD}Quick start:{NC}")
        mem_pfx = f"MEM_MCP_URL=http://localhost:{port} " if port != 8765 else ""
        print(f"    {mem_pfx}mem stats")
        print(f"    {mem_pfx}mem store myproject fact 0.8 'My first memory'")
        print(f"    {mem_pfx}mem search 'first memory'")
        nl()
        print(f"  {BOLD}Container management:{NC}")
        print(f"    docker compose logs -f oc-memory")
        print(f"    docker compose restart oc-memory")
        print(f"    docker compose down oc-memory")
    elif mode == "local-replica":
        ok(f"Local hybrid server: http://localhost:{port}/mcp")
        nl()
        print(f"  {BOLD}Services installed (launchd, macOS):{NC}")
        print("    launchctl list | grep oc-memory")
        print("    tail -f ~/.oc-memory/logs/com.oc-memory.litestream-follow.err.log")
        print("    tail -f ~/.oc-memory/logs/com.oc-memory.local-replica-server.err.log")
        nl()
        print(f"  {BOLD}Docs:{NC} docs/local-replica.md")
    else:
        ok("oc-memory installed (local Python mode)")
        nl()
        print(f"  {BOLD}Quick start:{NC}")
        print(f"    MEM_LOCAL=1 mem stats")
        print(f"    MEM_LOCAL=1 mem store myproject fact 0.8 'My first memory'")
        print(f"    MEM_LOCAL=1 mem search 'first memory'")
        nl()
        print(f"  {BOLD}Or use the oc-memory CLI directly:{NC}")
        print("    oc-memory store '{\"scene\":\"myproject\",\"cell_type\":\"fact\",\"salience\":0.8,\"content\":\"My first memory\"}'")
        print("    oc-memory search 'first memory'")
        print("    oc-memory stats")
        nl()
        print(f"  {BOLD}Start MCP server manually:{NC}")
        print("    oc-memory-mcp       # stdio (for MCP client configs)")

    nl()
    print(f"  {BOLD}Useful commands:{NC}")
    print("    oc-memory mcp-setup                  # reprint MCP config snippets")
    print("    oc-memory config --client claude      # JSON config for ~/.claude.json")
    print("    oc-memory config --client openclaw    # JSON config for openclaw.json")
    nl()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print_header()

    # ── Mode selection ────────────────────────────────────────────────────────
    if INSTALL_MODE == "docker":
        mode_idx = 0
    elif INSTALL_MODE == "local":
        mode_idx = 1
    elif INSTALL_MODE == "local-replica":
        mode_idx = 2
    else:
        mode_idx = menu(
            "How do you want to run oc-memory?",
            [
                ("Docker",        "Persistent MCP server in a container — shared across all tools"),
                ("Local Python",  "Install in your Python environment — single-user, no container"),
                ("Local Replica", "Litestream-synced local read copy of a remote server (fast reads, "
                                   "writes forwarded remotely)"),
            ],
            default=0,
        )

    docker_port: int | None = None

    if mode_idx == 0:
        # ── Docker path ───────────────────────────────────────────────────────
        docker_port = do_docker()
        mcp_entry = _http_entry(docker_port)
        nl()
        ok(f"Transport: Streamable HTTP  →  http://localhost:{docker_port}/mcp")
    elif mode_idx == 1:
        # ── Local Python path ─────────────────────────────────────────────────
        do_local()
        mcp_entry = _stdio_entry()
        nl()
        ok("Transport: stdio (direct process, no server port needed)")
    else:
        # ── Local Replica path ────────────────────────────────────────────────
        replica_port = do_local_replica()
        mcp_entry = _http_entry(replica_port)
        nl()
        ok(f"Transport: Streamable HTTP  →  http://localhost:{replica_port}/mcp (local replica)")

    # ── Client config ─────────────────────────────────────────────────────────
    do_client_config(mcp_entry)

    # ── Optional features ─────────────────────────────────────────────────────
    if mode_idx != 2:
        do_optional_features(docker_port)

    # ── Summary ───────────────────────────────────────────────────────────────
    mode_label = {0: "docker", 1: "local", 2: "local-replica"}[mode_idx]
    print_summary(mode_label, docker_port)


if __name__ == "__main__":
    main()
