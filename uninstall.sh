#!/usr/bin/env bash
#
# claude-context-meter uninstaller.
#
# Removes the Stop hook (and status line, if it points at context-meter) from
# ~/.claude/settings.json, leaving every other hook untouched. Installed files
# under ~/.claude/context-meter/ are kept unless you pass --purge.
#
# Usage:
#   ./uninstall.sh [--purge]
set -euo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

CLAUDE_DIR="$HOME/.claude"
DEST="$CLAUDE_DIR/context-meter"
SETTINGS="$CLAUDE_DIR/settings.json"

command -v jq >/dev/null 2>&1 || { echo "❌ jq not found" >&2; exit 1; }

if [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SETTINGS.context-meter.bak"
  tmp="$(mktemp)"
  jq '
    # remove our Stop-hook entries, keep all others
    if .hooks.Stop then
      .hooks.Stop = ([ .hooks.Stop[]
        | .hooks = ((.hooks // []) | map(select(.command | contains("context_meter.py") | not)))
      ] | map(select((.hooks | length) > 0)))
    else . end
    # drop the status line if it is ours
    | if (.statusLine.command // "") | contains("context-meter-statusline.sh")
      then del(.statusLine) else . end
  ' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  echo "→ Removed Stop hook / status line from settings.json"
  jq empty "$SETTINGS" && echo "→ settings.json is valid JSON"
fi

if [ "$PURGE" -eq 1 ]; then
  rm -rf "$DEST"
  echo "→ Purged $DEST"
else
  echo "→ Kept files under $DEST (pass --purge to delete them)"
fi
echo "✅ Uninstalled."
