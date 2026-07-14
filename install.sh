#!/usr/bin/env bash
#
# claude-context-meter installer.
#
# What it does:
#   1. Copies src/*.py to ~/.claude/context-meter/
#   2. Creates ~/.claude/context-meter/config.json from config.example.json
#      (only if it does not exist yet — your settings are never overwritten)
#   3. Registers the Stop hook in ~/.claude/settings.json (idempotent; leaves any
#      other hooks untouched; backs up settings.json first)
#   4. Warns if another Stop hook looks like an EARLIER context meter (a prior
#      install under a different name) that would run alongside this one
#   5. With --with-statusline: also wires up the optional status line
#
# Usage:
#   ./install.sh [--with-statusline] [--timeout N] [--replace-legacy]
#
#   --replace-legacy   remove detected earlier context-meter Stop hooks instead
#                      of only warning about them
#
# Re-running is safe: it updates the existing entry instead of duplicating it.
set -euo pipefail

WITH_STATUSLINE=0
REPLACE_LEGACY=0
TIMEOUT=10
while [ $# -gt 0 ]; do
  case "$1" in
    --with-statusline) WITH_STATUSLINE=1; shift ;;
    --replace-legacy) REPLACE_LEGACY=1; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
DEST="$CLAUDE_DIR/context-meter"
SETTINGS="$CLAUDE_DIR/settings.json"
HOOK_CMD="python3 \"$DEST/context_meter.py\""
STATUSLINE_CMD="$DEST/context-meter-statusline.sh"

# --- prerequisites ----------------------------------------------------------
command -v python3 >/dev/null 2>&1 || { echo "❌ python3 not found" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "❌ jq not found (needed to patch settings.json safely)" >&2; exit 1; }

echo "→ Installing claude-context-meter into $DEST"
mkdir -p "$DEST/state"
cp "$SCRIPT_DIR/src/context_meter.py" "$DEST/"
cp "$SCRIPT_DIR/src/usage.py"         "$DEST/"
cp "$SCRIPT_DIR/src/i18n.py"          "$DEST/"
cp "$SCRIPT_DIR/statusline/context-meter-statusline.sh" "$DEST/"
chmod +x "$DEST/context_meter.py" "$DEST/context-meter-statusline.sh"

# config.json: create from example only if absent (preserve user edits)
if [ ! -f "$DEST/config.json" ]; then
  cp "$SCRIPT_DIR/config.example.json" "$DEST/config.json"
  echo "→ Wrote default config: $DEST/config.json"
else
  echo "→ Keeping existing config: $DEST/config.json"
fi

# --- patch settings.json (idempotent) ---------------------------------------
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.context-meter.bak"
echo "→ Backed up settings to $SETTINGS.context-meter.bak"

tmp="$(mktemp)"
jq --arg cmd "$HOOK_CMD" --argjson timeout "$TIMEOUT" '
  # ensure hooks.Stop is an array
  .hooks = (.hooks // {})
  | .hooks.Stop = (.hooks.Stop // [])
  # drop any prior context-meter entry (match by command substring), keep others
  | .hooks.Stop = ([ .hooks.Stop[]
      | .hooks = ((.hooks // []) | map(select(.command | contains("context_meter.py") | not)))
    ] | map(select((.hooks | length) > 0)))
  # append our fresh entry
  | .hooks.Stop += [{ "matcher": "", "hooks": [
      { "type": "command", "command": $cmd, "timeout": $timeout } ] }]
' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
echo "→ Registered Stop hook"

# --- optional status line ---------------------------------------------------
if [ "$WITH_STATUSLINE" -eq 1 ]; then
  tmp="$(mktemp)"
  jq --arg cmd "$STATUSLINE_CMD" '
    .statusLine = { "type": "command", "command": $cmd, "padding": 1 }
  ' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  echo "→ Registered status line (sensor + display)"
fi

# --- detect / handle earlier context-meter hooks ----------------------------
# A previous version installed under a different name (e.g. session-context-alarm.py)
# would keep firing next to ours -> two blocks. Detect Stop-hook commands that look
# like a context meter (context/ctx + alarm/meter/monitor/gauge/hud) but are NOT
# ours. --replace-legacy removes them; otherwise we only warn.
LEGACY_RE='(context|ctx).*(alarm|meter|monitor|gauge|hud)'
legacy=$(jq -r --arg re "$LEGACY_RE" '
  .hooks.Stop[]?.hooks[]?.command
  | select(test("context_meter\\.py") | not)
  | select(test($re; "i"))
' "$SETTINGS" 2>/dev/null | sort -u || true)

if [ -n "$legacy" ]; then
  if [ "$REPLACE_LEGACY" -eq 1 ]; then
    tmp="$(mktemp)"
    jq --arg re "$LEGACY_RE" '
      .hooks.Stop = ([ .hooks.Stop[]
        | .hooks = ((.hooks // []) | map(select(
            (.command | test("context_meter\\.py"))          # keep ours
            or ((.command | test($re; "i")) | not)           # keep anything not meter-like
          )))
      ] | map(select((.hooks | length) > 0)))
    ' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
    echo "→ Removed earlier context-meter Stop hook(s):"
    echo "$legacy" | sed 's/^/    /'
  else
    echo ""
    echo "⚠️  Another Stop hook looks like an EARLIER context meter and will run"
    echo "    alongside this one (you'd see two blocks per reply):"
    echo "$legacy" | sed 's/^/      /'
    echo "    Re-run with --replace-legacy to remove it, or delete it by hand."
  fi
fi

# --- verify -----------------------------------------------------------------
python3 -m py_compile "$DEST/context_meter.py" "$DEST/usage.py" "$DEST/i18n.py"
jq empty "$SETTINGS" && echo "→ settings.json is valid JSON"

echo ""
echo "✅ Done. Start a new Claude Code session (or send a message) — the context"
echo "   block appears after the assistant's reply."
echo "   Edit $DEST/config.json to change language, thresholds, or features."
