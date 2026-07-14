#!/bin/bash
# claude-context-meter — OPTIONAL status line.
#
# Two jobs:
#  1. Sensor: Claude Code hands the status line the real context window
#     (.context_window.context_window_size). The Stop hook never receives that
#     field, so we record it per session under ~/.claude/context-meter/state/.
#     The hook prefers this exact value over its model-based guess.
#  2. Display: a one-line context bar for terminals that actually render a
#     status line. (The VS Code extension currently does not render it — there
#     the hook still works, using the model-based window detection.)
#
# Install by pointing settings.json -> statusLine.command at this file, or run
# install.sh with --with-statusline.
input=$(cat)

# --- Sensor -----------------------------------------------------------------
SID=$(echo "$input" | jq -r '.session_id // empty')
if [ -n "$SID" ]; then
  CWSIZE=$(echo "$input" | jq -r '
    .context_window.context_window_size
    // ( if (.context_window.used_percentage // 0) > 0
         then (.context_window.total_input_tokens / .context_window.used_percentage * 100 | round)
         else empty end )
    // empty')
  if [ -n "$CWSIZE" ] && [ "$CWSIZE" -gt 0 ] 2>/dev/null; then
    SDIR="$HOME/.claude/context-meter/state"
    mkdir -p "$SDIR"
    printf '%s' "$CWSIZE" > "$SDIR/$SID.window"
  fi
fi

# --- Display ----------------------------------------------------------------
MODEL=$(echo "$input" | jq -r '.model.display_name // "?"')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
TOK=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')
BRANCH=$(echo "$input" | jq -r '.workspace.git_branch // ""')

if   [ "$PCT" -ge 90 ]; then COL='\033[31m'   # red
elif [ "$PCT" -ge 70 ]; then COL='\033[33m'   # yellow
else                         COL='\033[32m'   # green
fi
RESET='\033[0m'; DIM='\033[2m'

W=10; FILLED=$((PCT * W / 100)); [ "$FILLED" -gt "$W" ] && FILLED=$W
EMPTY=$((W - FILLED)); BAR=""
[ "$FILLED" -gt 0 ] && { printf -v F "%${FILLED}s" ""; BAR="${F// /█}"; }
[ "$EMPTY"  -gt 0 ] && { printf -v E "%${EMPTY}s" "";  BAR="${BAR}${E// /░}"; }

if [ "$TOK" -ge 1000 ]; then TOKD="$((TOK / 1000))k"; else TOKD="$TOK"; fi
COSTD=$(printf '%.2f' "$COST" 2>/dev/null || echo "0.00")
GIT=""; [ -n "$BRANCH" ] && GIT=" ${DIM}· ${BRANCH}${RESET}"

printf "${DIM}[%s]${RESET} ${COL}%s %s%%${RESET} ${DIM}· %s tok · \$%s${RESET}%b\n" \
  "$MODEL" "$BAR" "$PCT" "$TOKD" "$COSTD" "$GIT"
