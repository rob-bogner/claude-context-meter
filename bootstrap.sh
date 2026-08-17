#!/usr/bin/env bash
#
# One-line bootstrap for claude-context-meter.
#
#   curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash
#
# Pass installer options through with `bash -s --`:
#   curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash -s -- --in-place
#
# Uninstall with the same one-liner — `--uninstall` runs uninstall.sh instead of
# the installer, and `--purge` / `--dry-run` are passed on to it:
#   curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash -s -- --uninstall
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

# --- mode: install (default) or uninstall -----------------------------------
MODE="install"
ARGS=()
for a in "$@"; do
  case "$a" in
    --uninstall) MODE="uninstall" ;;
    *) ARGS+=("$a") ;;
  esac
done

if [ "$MODE" = "uninstall" ]; then
  # Deregistering needs the repo scripts. Reuse an existing clone as-is (no pull
  # — the network must not stand between you and an uninstall). Without one,
  # fetch into a temp dir and remove it afterwards, so uninstalling never leaves
  # a clone behind.
  if [ -d "$SRC_DIR/.git" ]; then
    echo "→ Uninstalling with the existing clone in $SRC_DIR"
    exec bash "$SRC_DIR/uninstall.sh" ${ARGS+"${ARGS[@]}"}
  fi
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  echo "→ No clone found — fetching the uninstaller into a temporary directory"
  git clone --depth 1 --quiet "$REPO_URL" "$TMP_DIR/repo"
  bash "$TMP_DIR/repo/uninstall.sh" ${ARGS+"${ARGS[@]}"}
  exit 0
fi

if [ -d "$SRC_DIR/.git" ]; then
  echo "→ Updating existing clone in $SRC_DIR"
  git -C "$SRC_DIR" pull --ff-only --quiet
else
  echo "→ Cloning into $SRC_DIR"
  mkdir -p "$(dirname "$SRC_DIR")"
  git clone --depth 1 --quiet "$REPO_URL" "$SRC_DIR"
fi

exec bash "$SRC_DIR/install.sh" ${ARGS+"${ARGS[@]}"}
