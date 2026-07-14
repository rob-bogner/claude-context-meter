#!/usr/bin/env python3
"""claude-context-meter — a Claude Code Stop hook.

Fires after every assistant reply and prints a compact, at-a-glance dashboard as
the assistant's next message:

  🟢 Context 🟩🟩🟩🟨🟨🟨🟧⬛…  32% · 318k/1M · 💰 $0.42 · ⇡4 unpushed
  📊 Session 🟩🟩⬛…  10% (↻3h) · Week 16% (↻5d) · Sonnet 2% (↻5d)
  💡 Keep an eye on it

Line 1 (Context) is always shown. Line 2 (subscription usage) is optional and
needs a valid OAuth token (see usage.py) — it is silently dropped otherwise.
Line 3 is a one-line recommendation.

The window size (200k vs 1M) is detected WITHOUT relying on the status line —
see read_window(). Everything visible is configurable via config.json; every word
comes from i18n.py.

How the block reaches the chat: a Stop hook may return {"decision":"block", ...};
Claude Code then continues the turn and the assistant emits the block. The
`stop_hook_active` guard stops that from looping forever.
"""
import sys, os, json, subprocess, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from i18n import translator
except Exception:                                   # pragma: no cover
    def translator(_lang):
        return lambda k: k

try:
    from usage import get_usage, fmt_reset          # optional line 2
except Exception:
    def get_usage():
        return None

    def fmt_reset(_iso, _now_word="now"):
        return None

HOME = os.path.expanduser("~")
BASE_DIR = os.path.join(HOME, ".claude", "context-meter")
STATE_DIR = os.path.join(BASE_DIR, "state")
CONFIG_PATH = os.environ.get("CONTEXT_METER_CONFIG", os.path.join(BASE_DIR, "config.json"))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULTS = {
    "language": "en",
    "bands": [15, 30, 45],          # green <15 · yellow 15–30 · orange 30–45 · red ≥45
    "display_min_tokens": 6000,     # stay silent below this absolute context load
    "segments": 20,                 # bar length (20 × 5% = 5% resolution)
    # How the block reaches the chat:
    #   "system" — one systemMessage; shown once, assistant does NOT repeat it.
    #              Looks identical in the IDE and the terminal. (recommended)
    #   "block"  — decision:block; the assistant re-emits the block. Renders as a
    #              chat bubble in the IDE, but the CLI also shows the hook feedback,
    #              so the block appears TWICE in the terminal.
    "output_mode": "system",
    "features": {
        "cost": True,
        "usage": True,
        "git_ahead": True,
        "sound": True,
    },
    # Substring of the transcript model id -> context window in tokens.
    # First match wins, so list more specific keys first.
    "model_windows": {
        "opus-4-8": 1000000,
        "fable-5": 1000000,
        "sonnet": 200000,
        "haiku": 200000,
    },
    # USD per million tokens. Defaults are Claude Opus 4.8 pricing.
    "prices_per_mtok": {
        "input": 5.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
        "output": 25.00,
    },
    # macOS system sounds played on the up-transition into a band (null = silent).
    "sounds": {
        "orange": "/System/Library/Sounds/Tink.aiff",
        "red": "/System/Library/Sounds/Sosumi.aiff",
    },
}


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    cfg = DEFAULTS
    try:
        with open(CONFIG_PATH) as f:
            cfg = _deep_merge(DEFAULTS, json.load(f))
    except Exception:
        pass
    # Env overrides (handy for tests / one-off runs).
    if os.environ.get("CONTEXT_METER_LANG"):
        cfg["language"] = os.environ["CONTEXT_METER_LANG"]
    raw_bands = os.environ.get("CONTEXT_METER_BANDS")
    if raw_bands:
        try:
            xs = [int(x) for x in raw_bands.split(",") if x.strip()]
            if len(xs) == 3:
                cfg["bands"] = xs
        except Exception:
            pass
    return cfg


KNOWN_WINDOWS = (200_000, 1_000_000)   # known window tiers for the empirical safety net


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def tier(pct, bands, t):
    """Leading emoji / sound-key / recommendation by context zone."""
    g, y, o = bands
    if pct >= o:
        return ("\U0001F534", "red", t("hint_red"))       # 🔴
    if pct >= y:
        return ("\U0001F7E0", "orange", t("hint_orange"))  # 🟠
    if pct >= g:
        return ("\U0001F7E1", None, t("hint_yellow"))      # 🟡
    return ("\U0001F7E2", None, t("hint_green"))           # 🟢


def band_index(pct, bands):
    """Number of thresholds crossed = band (0 green .. 3 red) — for sound de-dupe."""
    return sum(1 for th in bands if pct >= th)


def gradient_bar(pct, bands, segments):
    """Segmented bar; each filled cell colored for ITS OWN zone, empty = ⬛."""
    g, y, o = bands
    pct = max(0, int(pct))
    filled = min(segments, (pct + 4) // 5)   # ceil(pct/5): a started 5% step counts full
    out = []
    for i in range(1, segments + 1):
        if i > filled:
            out.append("⬛")             # ⬛ empty track
            continue
        upper = i * 5                         # this cell's upper bound in %
        if upper <= g:
            out.append("\U0001F7E9")          # 🟩 green
        elif upper <= y:
            out.append("\U0001F7E8")          # 🟨 yellow
        elif upper <= o:
            out.append("\U0001F7E7")          # 🟧 orange
        else:
            out.append("\U0001F7E5")          # 🟥 red
    return "".join(out)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------
def _iter_assistant_usages(path):
    """Yield every assistant message.usage object from the JSONL transcript."""
    try:
        with open(path, "rb") as f:
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return
    for line in data.splitlines():
        line = line.strip()
        if not line or '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        m = d.get("message") or {}
        if m.get("role") != "assistant":
            continue
        u = m.get("usage")
        if isinstance(u, dict):
            yield u


def _n(x):
    return x if isinstance(x, (int, float)) else 0


def last_context_tokens(path):
    """Last assistant usage = what is really loaded in the context window."""
    last = None
    for u in _iter_assistant_usages(path):
        tok = (_n(u.get("input_tokens"))
               + _n(u.get("cache_read_input_tokens"))
               + _n(u.get("cache_creation_input_tokens")))
        if tok > 0:
            last = tok
    return last


def last_model(path):
    """Model id of the last real assistant message (ignoring <synthetic>)."""
    try:
        with open(path, "rb") as f:
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    last = None
    for line in data.splitlines():
        line = line.strip()
        if not line or '"model"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        m = d.get("message") or {}
        if m.get("role") == "assistant":
            mm = m.get("model")
            if isinstance(mm, str) and mm and mm != "<synthetic>":
                last = mm
    return last


def read_window(sid, transcript, model_windows, observed_tokens=0):
    """Resolve the context window in priority order — status-line independent.

    Why not read the model from settings: Claude Code does not persist the active
    model anywhere a hook can read, and the transcript stores the base id WITHOUT the
    "[1m]" marker (e.g. "claude-opus-4-8"). So we map the model FAMILY to its window.

      1) Status-line sensor file (if present, e.g. terminal client): the exact
         context_window_size Claude Code hands the status line — takes precedence.
      2) Model from the transcript -> model_windows (the normal case in the IDE).
      3) Empirical net: the context can never hold more tokens than the window is
         large, so observed tokens lift the window to the next known tier.
      4) Fallback 200000.
    """
    win = 0
    try:
        with open(os.path.join(STATE_DIR, sid + ".window")) as f:
            win = int(f.read().strip() or "0")
    except Exception:
        win = 0

    if win <= 0:
        model = (last_model(transcript) or "").lower()
        for key, w in model_windows.items():
            if key.lower() in model:
                win = int(w)
                break

    floor = 200_000
    for k in KNOWN_WINDOWS:
        if observed_tokens <= k:
            floor = k
            break
    else:
        floor = observed_tokens          # larger than any known tier

    if win < floor:
        win = floor
    return win if win > 0 else 200_000


def session_cost(path, prices):
    """Estimated session cost (USD) across all turns; None if nothing found."""
    pin = prices["input"] / 1_000_000
    pcw = prices["cache_write"] / 1_000_000
    pcr = prices["cache_read"] / 1_000_000
    pout = prices["output"] / 1_000_000
    total, found = 0.0, False
    for u in _iter_assistant_usages(path):
        total += (_n(u.get("input_tokens")) * pin
                  + _n(u.get("cache_creation_input_tokens")) * pcw
                  + _n(u.get("cache_read_input_tokens")) * pcr
                  + _n(u.get("output_tokens")) * pout)
        found = True
    return total if found else None


def git_ahead(cwd):
    """Number of local commits not yet pushed (or None)."""
    if not cwd:
        return None
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-list", "--count", "@{u}..HEAD"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return int(r.stdout.strip() or "0")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Line 2 — subscription usage
# ---------------------------------------------------------------------------
def _fmt_window(label, w, with_bar, bands, segments, t):
    if not isinstance(w, dict):
        return None
    p = w.get("pct")
    if not isinstance(p, (int, float)):
        return None
    p = int(round(p))
    r = fmt_reset(w.get("resets_at", ""), t("reset_now"))
    cd = " (↻%s)" % r if r else ""       # ↻ = cooldown until reset
    if with_bar:
        return "%s %s %d%%%s" % (label, gradient_bar(p, bands, segments), p, cd)
    return "%s %d%%%s" % (label, p, cd)


def usage_block(bands, segments, t):
    """Line 2: Session (with bar) · Week · Sonnet, each with cooldown. None if no data."""
    try:
        u = get_usage()
    except Exception:
        u = None
    if not u:
        return None
    parts = [
        _fmt_window(t("session"), u.get("five_hour"), True, bands, segments, t),
        _fmt_window(t("week"), u.get("seven_day"), False, bands, segments, t),
        _fmt_window(t("sonnet_week"), u.get("seven_day_sonnet"), False, bands, segments, t),
    ]
    parts = [p for p in parts if p]
    if not parts:
        return None
    return "\U0001F4CA " + " · ".join(parts)


def play(sound):
    if not sound:
        return
    try:
        subprocess.Popen(["afplay", sound],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_block(cfg, t, tokens, window, cost, ahead, usage_line):
    bands = cfg["bands"]
    segments = cfg["segments"]
    pct = round(tokens / window * 100)
    emoji, _sound_key, hint = tier(pct, bands, t)

    wl = "1M" if window >= 1_000_000 else "%dk" % (window // 1000)
    tk = "%.1fM" % (tokens / 1_000_000) if tokens >= 1_000_000 else "%dk" % round(tokens / 1000)
    line1 = "%s %s %s %d%% · %s/%s" % (
        emoji, t("context"), gradient_bar(pct, bands, segments), pct, tk, wl)
    if cost is not None:
        line1 += " · \U0001F4B0 $%.2f" % cost
    if ahead:
        line1 += " · ⇡%d %s" % (ahead, t("unpushed"))

    line3 = "\U0001F4A1 %s" % hint
    lines = [line1] + ([usage_line] if usage_line else []) + [line3]
    return "\n".join(lines)


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    # Loop guard: after our own decision:block, the hook fires again with
    # stop_hook_active=true -> do nothing (no second alarm/block).
    if ev.get("stop_hook_active"):
        return

    sid = ev.get("session_id") or "unknown"
    tpath = ev.get("transcript_path")
    cwd = ev.get("cwd") or ""
    if not tpath or not os.path.exists(tpath):
        return

    cfg = load_config()
    t = translator(cfg.get("language"))
    bands = cfg["bands"]
    feats = cfg.get("features", {})

    tokens = last_context_tokens(tpath)
    if not tokens:
        time.sleep(0.4)                  # race on the 1st turn: usage may not be written yet
        tokens = last_context_tokens(tpath)
    if not tokens:
        return
    if tokens < cfg.get("display_min_tokens", 6000):
        return

    window = read_window(sid, tpath, cfg["model_windows"], tokens)
    pct = round(tokens / window * 100)
    b = band_index(pct, bands)

    # Sound de-dupe: play only on the up-transition into a new band.
    os.makedirs(STATE_DIR, exist_ok=True)
    statef = os.path.join(STATE_DIR, sid + ".band")
    prev = -1
    try:
        with open(statef) as f:
            prev = int(f.read().strip() or "-1")
    except Exception:
        prev = -1
    try:
        with open(statef, "w") as f:
            f.write(str(b))
    except Exception:
        pass

    _emoji, sound_key, _hint = tier(pct, bands, t)
    if feats.get("sound", True) and b > prev and sound_key:
        play(cfg.get("sounds", {}).get(sound_key))

    cost = session_cost(tpath, cfg["prices_per_mtok"]) if feats.get("cost", True) else None
    ahead = git_ahead(cwd) if feats.get("git_ahead", True) else None
    usage_line = usage_block(bands, cfg["segments"], t) if feats.get("usage", True) else None

    block = build_block(cfg, t, tokens, window, cost, ahead, usage_line)
    if cfg.get("output_mode", "system") == "block":
        # Assistant re-emits the block. Nice chat bubble in the IDE, but the CLI
        # also renders the hook feedback -> the block shows up twice there.
        out = {"decision": "block", "reason": t("instruction").format(block=block)}
    else:
        # Shown once as a system message; the assistant is not asked to repeat it.
        # Same result in the IDE and the terminal.
        out = {"systemMessage": block}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
