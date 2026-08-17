#!/usr/bin/env bash
#
# claude-context-meter installer.
#
# What it does:
#   1. Copies src/*.py to ~/.claude/context-meter/
#   2. Creates ~/.claude/context-meter/config.json from config.example.json
#      (only if it does not exist yet — your settings are never overwritten)
#   3. Registers three entries in ~/.claude/settings.json, via
#      src/install_settings.py (idempotent, backs up settings.json first):
#        · statusLine   → the sensor. NOT optional: it is the only place Claude
#                         Code exposes the real context window size. An existing
#                         status line is wrapped, never replaced.
#        · Stop         → the dashboard
#        · SessionStart → the model line
#      Earlier context-meter Stop hooks (including installs under a different
#      name) are replaced; unrelated hooks are left untouched.
#   4. Runs the self-check (src/doctor.py)
#
# Usage:
#   ./install.sh [--no-statusline] [--in-place] [--dry-run]
#
#   --no-statusline  register hooks only. The meter then relies on the Models API
#                    to resolve the window (cascade level S4) and degrades to a
#                    plain token count where that is unavailable.
#   --in-place       point the hooks at this checkout instead of copying to
#                    ~/.claude/context-meter — `git pull` then updates the install.
#   --dry-run        show what would change in settings.json, write nothing.
#
# Re-running is safe: entries are updated, not duplicated.
set -euo pipefail

NO_STATUSLINE=0
IN_PLACE=0
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-statusline) NO_STATUSLINE=1; shift ;;
    --in-place)      IN_PLACE=1; shift ;;
    --dry-run)       DRY_RUN=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
DEST="$CLAUDE_DIR/context-meter"

# --- prerequisites ----------------------------------------------------------
# No jq: settings.json is patched by install_settings.py (Python stdlib only).
command -v python3 >/dev/null 2>&1 || { echo "❌ python3 not found" >&2; exit 1; }

if [ "$IN_PLACE" -eq 1 ]; then
  BASE="$SCRIPT_DIR"
  echo "→ Installing in place: hooks will point at $BASE"
  mkdir -p "$DEST/state"
else
  BASE="$DEST"
  echo "→ Installing claude-context-meter into $DEST"
  mkdir -p "$DEST/src" "$DEST/state"
  cp "$SCRIPT_DIR"/src/*.py "$DEST/src/"
  chmod +x "$DEST"/src/*.py
fi

# config.json: create from example only if absent (preserve user edits)
if [ ! -f "$DEST/config.json" ]; then
  cp "$SCRIPT_DIR/config.example.json" "$DEST/config.json"
  echo "→ Wrote default config: $DEST/config.json"
else
  echo "→ Keeping existing config: $DEST/config.json"
fi

# --- register status line + hooks -------------------------------------------
ARGS=("$BASE")
[ "$DRY_RUN" -eq 1 ] && ARGS+=(--dry-run)
python3 "$SCRIPT_DIR/src/install_settings.py" "${ARGS[@]}"

if [ "$NO_STATUSLINE" -eq 1 ] && [ "$DRY_RUN" -eq 0 ]; then
  python3 - "$CLAUDE_DIR/settings.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
sl = d.get("statusLine") or {}
if "sensor.py" in (sl.get("command") or ""):
    d.pop("statusLine", None)
    json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
    open(p, "a").write("\n")
    print("  Status-Line auf Wunsch nicht registriert (--no-statusline)")
PY
fi

[ "$DRY_RUN" -eq 1 ] && exit 0

# --- verify -----------------------------------------------------------------
python3 -m py_compile "$BASE"/src/*.py
python3 -c "import json,sys; json.load(open('$CLAUDE_DIR/settings.json')); print('→ settings.json is valid JSON')"

echo ""
python3 "$BASE/src/doctor.py" || true

echo ""
echo "✅ Done. Send a message — the block appears after the assistant's reply."
echo "   The sensor writes on the next assistant message; if the status line"
echo "   never runs, restart the client (the registration is read at startup)."
echo "   Edit $DEST/config.json to change language, thresholds, or features."
