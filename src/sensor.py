#!/usr/bin/env python3
"""claude-context-meter — the sensor (status-line command).

On every new assistant message Claude Code hands the status-line command a JSON
object on stdin containing the only authoritative runtime values of the session
— above all `context_window.context_window_size`, the REAL window size. No hook
event carries that value, and the model id in the transcript cannot tell 200k
from 1M (it has no `[1m]` suffix). This file is therefore the foundation of the
whole project: it measures, everything else merely renders.

Two jobs:

  1. SENSOR — writes the complete state as JSON to
     ~/.claude/context-meter/state/<session_id>.json (atomically via tmp+rename,
     so a hook reading concurrently never sees half a file) and to
     state/last.json, so the SessionStart hook has something to show before the
     first turn.

  2. DISPLAY — prints a compact line. Terminals render it; the VS Code extension
     currently shows no status line, so there only the side effect from (1)
     counts.

Deliberately without `jq`: plain Python 3 saves a dependency that is often
missing on fresh systems, and costs only ~40 ms per call (status-line updates
are debounced by 300 ms).

The sensor writes ONLY what Claude Code supplied. A missing field stays `null` —
nothing is estimated, guessed, or derived from the model name. Model
independence hangs on exactly that: a future model with an unknown name still
reports its window size correctly here.
"""
import sys, os, json, time

SCHEMA = 1
HOME = os.path.expanduser("~")
STATE_DIR = os.environ.get(
    "CONTEXT_METER_STATE", os.path.join(HOME, ".claude", "context-meter", "state")
)


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _dig(d, *path):
    """Fetch a nested value; None as soon as a link is missing or not a dict."""
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _window(cw):
    """Window size: prefer the explicit field, otherwise reconstruct it from
    tokens and percentage (Claude Code supplies `used_percentage` even where an
    older version still lacks `context_window_size`)."""
    w = _num(_dig(cw, "context_window_size"))
    if w and w > 0:
        return int(w)
    tok, pct = _num(_dig(cw, "total_input_tokens")), _num(_dig(cw, "used_percentage"))
    if tok and pct and pct > 0:
        return int(round(tok / pct * 100))
    return None


def _limits(rl):
    """Reduce rate_limits to what is needed. `resets_at` is Unix epoch seconds."""
    out = {}
    for key in ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus"):
        w = _dig(rl, key)
        if not isinstance(w, dict):
            continue
        pct = _num(w.get("used_percentage"))
        if pct is None:
            continue
        out[key] = {"pct": pct, "resets_at": _num(w.get("resets_at"))}
    return out or None


def build_state(ev):
    cw = _dig(ev, "context_window") or {}
    return {
        "schema": SCHEMA,
        "ts": time.time(),
        "session_id": ev.get("session_id"),
        # --- the authoritative values ---
        "window": _window(cw),
        "tokens_in": _num(cw.get("total_input_tokens")),
        "tokens_out": _num(cw.get("total_output_tokens")),
        "used_pct": _num(cw.get("used_percentage")),
        "exceeds_200k": ev.get("exceeds_200k_tokens"),
        # --- model: display name and id, NOT for deriving the window ---
        "model_id": _dig(ev, "model", "id"),
        "model_name": _dig(ev, "model", "display_name"),
        # --- Claude Code computes the cost itself; no local price table needed ---
        "cost_usd": _num(_dig(ev, "cost", "total_cost_usd")),
        # --- subscription usage: fully replaces the OAuth/keychain path ---
        "rate_limits": _limits(_dig(ev, "rate_limits")),
        # --- context for rendering ---
        "effort": _dig(ev, "effort", "level"),
        "fast_mode": ev.get("fast_mode"),
        "thinking": _dig(ev, "thinking", "enabled"),
        "version": ev.get("version"),
        "cwd": _dig(ev, "workspace", "current_dir") or ev.get("cwd"),
        "git_branch": _dig(ev, "workspace", "git_branch"),
    }


def write_state(state):
    """Write atomically: tmp first, then rename. os.replace is atomic on POSIX,
    so a Stop hook reading in parallel always sees a complete file."""
    sid = state.get("session_id")
    if not sid:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        blob = json.dumps(state, separators=(",", ":"))
        for name in (sid + ".json", "last.json"):
            path = os.path.join(STATE_DIR, name)
            tmp = path + ".tmp.%d" % os.getpid()
            with open(tmp, "w") as f:
                f.write(blob)
            os.replace(tmp, path)
    except Exception:
        pass  # A sensor failure must never bring the status line down.


# ---------------------------------------------------------------------------
# Display (visible only where Claude Code renders a status line)
# ---------------------------------------------------------------------------
DIM, RESET = "\033[2m", "\033[0m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"


def render(state):
    win, tok = state.get("window"), state.get("tokens_in")
    parts = []

    name = state.get("model_name") or state.get("model_id") or "?"
    wl = "" if not win else (" 1M" if win >= 1_000_000 else " %dk" % (win // 1000))
    parts.append("%s[%s%s]%s" % (DIM, name, wl, RESET))

    if win and tok:
        pct = tok / win * 100
        col = RED if pct >= 75 else (YELLOW if pct >= 45 else GREEN)
        filled = min(10, int(pct / 10 + 0.999))
        bar = "█" * filled + "░" * (10 - filled)
        parts.append("%s%s %d%%%s" % (col, bar, round(pct), RESET))
        parts.append("%s%dk tok%s" % (DIM, round(tok / 1000), RESET))
    elif tok:
        parts.append("%s%dk tok%s" % (DIM, round(tok / 1000), RESET))

    cost = state.get("cost_usd")
    if cost is not None:
        parts.append("%s$%.2f%s" % (DIM, cost, RESET))

    branch = state.get("git_branch")
    if branch:
        parts.append("%s%s%s" % (DIM, branch, RESET))

    return " ".join(parts)


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    state = build_state(ev)
    write_state(state)
    try:
        print(render(state))
    except Exception:
        pass


if __name__ == "__main__":
    main()
