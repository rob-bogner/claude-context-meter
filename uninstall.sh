#!/usr/bin/env bash
#
# claude-context-meter uninstaller.
#
# Removes everything the installer registered in ~/.claude/settings.json —
# the status line (sensor), the Stop hook (dashboard) and the SessionStart hook
# (model line) — and leaves every unrelated entry untouched. The removal itself
# is done by src/install_settings.py --uninstall, the same module that wrote the
# entries, so both sides agree on what "ours" means. Python stdlib only, no jq.
#
# Installed files under ~/.claude/context-meter/ are kept unless you pass
# --purge. A repository clone is never deleted — you may be running from it.
#
# Usage:
#   ./uninstall.sh [--purge] [--dry-run]
#
#   --purge    also delete ~/.claude/context-meter/ (scripts, config, state)
#   --dry-run  show what would change in settings.json, write nothing
#
# Without a checkout, uninstall via the one-liner:
#   curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash -s -- --uninstall
set -euo pipefail

PURGE=0
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --purge)   PURGE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

CLAUDE_DIR="$HOME/.claude"
DEST="$CLAUDE_DIR/context-meter"

command -v python3 >/dev/null 2>&1 || { echo "❌ python3 not found" >&2; exit 1; }

# --- locate install_settings.py ---------------------------------------------
# Next to this script (a checkout), or in the standard install target. When this
# script is piped into bash, BASH_SOURCE is not a path — guard against that.
SCRIPT_DIR=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

INSTALLER=""
for cand in "${SCRIPT_DIR:+$SCRIPT_DIR/src/install_settings.py}" \
            "$DEST/src/install_settings.py"; do
  [ -n "$cand" ] && [ -f "$cand" ] && { INSTALLER="$cand"; break; }
done

if [ -z "$INSTALLER" ]; then
  cat >&2 <<EOF
❌ install_settings.py not found — nothing was changed.

   Looked in:
     ${SCRIPT_DIR:-<not a checkout>}/src/
     $DEST/src/

   Run this from a clone of the repository, or use the one-liner:
     curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash -s -- --uninstall
EOF
  exit 1
fi

# --- deregister --------------------------------------------------------------
ARGS=(--uninstall)
[ "$DRY_RUN" -eq 1 ] && ARGS+=(--dry-run)
python3 "$INSTALLER" "${ARGS[@]}"

if [ "$DRY_RUN" -eq 1 ]; then
  echo ""
  echo "→ Dry run — settings.json unchanged, no files removed."
  exit 0
fi

python3 -c "import json,sys; p='$CLAUDE_DIR/settings.json'; json.load(open(p)); print('→ settings.json is valid JSON')" 2>/dev/null \
  || echo "→ No settings.json (nothing to validate)"

# --- files -------------------------------------------------------------------
if [ "$PURGE" -eq 1 ]; then
  rm -rf "$DEST"
  echo "→ Purged $DEST"
else
  [ -d "$DEST" ] && echo "→ Kept files under $DEST (pass --purge to delete them)"
fi

CLONE="${CONTEXT_METER_SRC:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-context-meter}"
[ -d "$CLONE/.git" ] && echo "→ Clone left in place: $CLONE (delete it yourself if you want it gone)"

echo "✅ Uninstalled. Restart the client — the registration is read at startup."
