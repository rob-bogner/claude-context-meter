#!/usr/bin/env bash
#
# One-line bootstrap for claude-context-meter.
#
#   curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash
#
# Pass installer options through with `bash -s --`:
#   curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash -s -- --with-statusline
#
# It clones (or updates) the repo into ~/.local/share/claude-context-meter and
# runs install.sh from there. Re-running is an update: it fast-forwards and
# re-installs, keeping your config.json. Override locations with env vars:
#   CONTEXT_METER_REPO  — git URL to clone (default: this repo)
#   CONTEXT_METER_SRC   — where to keep the clone
set -euo pipefail

REPO_URL="${CONTEXT_METER_REPO:-https://github.com/rob-bogner/claude-context-meter.git}"
SRC_DIR="${CONTEXT_METER_SRC:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-context-meter}"

command -v git >/dev/null 2>&1 || { echo "❌ git is required" >&2; exit 1; }

if [ -d "$SRC_DIR/.git" ]; then
  echo "→ Updating existing clone in $SRC_DIR"
  git -C "$SRC_DIR" pull --ff-only --quiet
else
  echo "→ Cloning into $SRC_DIR"
  mkdir -p "$(dirname "$SRC_DIR")"
  git clone --depth 1 --quiet "$REPO_URL" "$SRC_DIR"
fi

exec bash "$SRC_DIR/install.sh" "$@"
