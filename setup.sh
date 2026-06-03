#!/usr/bin/env bash
# setup.sh — thin launcher for the oc-memory TUI installer
#
# Usage:
#   bash setup.sh [--yes] [--local | --docker]
#
# For non-interactive (CI / container) installs:
#   NONINTERACTIVE=1 INSTALL_MODE=local bash setup.sh
#
# See install.py for all options and environment variables.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/install.py" "$@"
