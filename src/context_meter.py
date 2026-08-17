#!/usr/bin/env python3
"""claude-context-meter — the Stop hook (renderer).

Fires after every assistant reply and prints a compact dashboard:

  🧠 Opus 5 · 1M window · effort xhigh
  🟢 Context 🟩🟩🟩🟨⬛…  20% · 201k/1M · 💰 $0.42 · ⇡4 unpushed
  📊 Session 🟩🟩⬛…  10% (↻3h) · Week 16% (↻5d)
  💡 All clear

This file COMPUTES nothing it can measure instead: window size, tokens, cost,
model name and subscription usage come from the sensor (`sensor.py`, registered
as the status line). `context.py` resolves which level of the cascade applies;
this file only renders.

The central difference to earlier versions: if the window size is NOT measured,
**no percentage** is printed — no colour band, no sound, no handoff advice. A
percentage without a known window is a claim, and that claim is what used to
raise false alarms ("100% · 201k/200k" while 20% of a 1M window was actually
in use).

Nothing breaks without a sensor; it simply claims less, honestly:

  ⚪ Context 201k loaded · window unknown (≥1M)
  💡 Status line inactive — run `doctor`

How the block reaches the chat: a Stop hook may return {"decision":"block", ...};
Claude Code then continues the turn and the assistant prints the block. The
`stop_hook_active` guard prevents the endless loop.
"""
import sys, os, json, subprocess, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import resolve, floor_for, diagnose, STATE_DIR  # noqa: E402

try:
    from i18n import translator
except Exception:                                   # pragma: no cover
    def translator(_lang):
        return lambda k: k

try:
    from usage import get_usage, fmt_reset          # fallback only, when no sensor runs
except Exception:
    def get_usage():
        return None

    def fmt_reset(_iso, _now_word="now"):
        return None

HOME = os.path.expanduser("~")
BASE_DIR = os.path.join(HOME, ".claude", "context-meter")
CONFIG_PATH = os.environ.get("CONTEXT_METER_CONFIG", os.path.join(BASE_DIR, "config.json"))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULTS = {
    "language": "en",
    # Thresholds in percent of the REAL window. They now refer to the measured
    # window size rather than an assumed 200k denominator: on 1M, 15% means
    # 150k tokens loaded.
    "bands": [15, 30, 45],          # green <15 · yellow 15–30 · orange 30–45 · red ≥45
    "display_min_tokens": 6000,     # stay silent below this absolute load
    "segments": 20,                 # bar length (20 × 5% = 5% resolution)
    # How the block reaches the chat:
    #   "auto"   — detect the client (recommended): IDE extensions render
    #              decision:block as a clean chat bubble; the terminal also shows
    #              the hook feedback, so systemMessage is used there.
    #   "block"  — always decision:block.
    #   "system" — always systemMessage.
    "output_mode": "auto",
    "clients": ["ide", "terminal"],
    "features": {
        "model_line": True,         # line 1: model + window
        "cost": True,
        "usage": True,
        "git_ahead": True,
        "sound": True,
    },
    # Window size when no sensor runs (level S3 of the cascade). 0 = off.
    # Only set this when it is genuinely known — a wrong number here is worse
    # than none, because it claims percentages again.
    "window_override": 0,
    "sensor_fresh_secs": 90,
    # USD per million tokens — a fallback only, for when the sensor supplies no
    # cost. Otherwise Claude Code does the maths itself (cost.total_cost_usd).
    # Cache writes are weighted by TTL: 5m = 1.25× input, 1h = 2.0× input.
    "prices_per_mtok": {
        "default": {"input": 5.00, "output": 25.00},
        "fable":   {"input": 10.00, "output": 50.00},
        "haiku":   {"input": 1.00, "output": 5.00},
        "sonnet":  {"input": 3.00, "output": 15.00},
    },
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


_IDE_ENTRYPOINTS = ("vscode", "jetbrains", "intellij", "pycharm", "idea")


def current_client():
    ep = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").lower()
    return "ide" if any(k in ep for k in _IDE_ENTRYPOINTS) else "terminal"


def resolve_output_mode(cfg):
    mode = cfg.get("output_mode", "auto")
    if mode in ("block", "system"):
        return mode
    return "block" if current_client() == "ide" else "system"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def tier(pct, bands, t):
    g, y, o = bands
    if pct >= o:
        return ("\U0001F534", "red", t("hint_red"))        # 🔴
    if pct >= y:
        return ("\U0001F7E0", "orange", t("hint_orange"))  # 🟠
    if pct >= g:
        return ("\U0001F7E1", None, t("hint_yellow"))      # 🟡
    return ("\U0001F7E2", None, t("hint_green"))           # 🟢


def band_index(pct, bands):
    return sum(1 for th in bands if pct >= th)


def gradient_bar(pct, bands, segments):
    """Segmented bar; each filled cell in the colour of ITS zone, empty = ⬛."""
    g, y, o = bands
    pct = max(0, int(pct))
    filled = min(segments, (pct + 4) // 5)   # ceil(pct/5)
    out = []
    for i in range(1, segments + 1):
        if i > filled:
            out.append("⬛")
            continue
        upper = i * 5
        if upper <= g:
            out.append("\U0001F7E9")          # 🟩
        elif upper <= y:
            out.append("\U0001F7E8")          # 🟨
        elif upper <= o:
            out.append("\U0001F7E7")          # 🟧
        else:
            out.append("\U0001F7E5")          # 🟥
    return "".join(out)


def fmt_tokens(n):
    if not n:
        return "0"
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    return "%dk" % round(n / 1000)


def fmt_window(w):
    if not w:
        return "?"
    if w >= 1_000_000:
        return "%gM" % (w / 1_000_000)
    return "%dk" % (w // 1000)


# ---------------------------------------------------------------------------
# Transcript — the fallback for when no sensor runs
# ---------------------------------------------------------------------------
def _iter_assistant_lines(path, needle=None):
    """Every assistant line of the transcript, as (whole entry, message).

    `needle` is a cheap pre-filter: skipping lines by substring before parsing
    them as JSON matters on transcripts of several thousand entries.
    """
    try:
        with open(path, "rb") as f:
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return
    for line in data.splitlines():
        line = line.strip()
        if not line or (needle and needle not in line):
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        m = d.get("message") or {}
        if isinstance(m, dict) and m.get("role") == "assistant":
            yield d, m


def _iter_assistant_usages(path):
    for _entry, m in _iter_assistant_lines(path, '"usage"'):
        u = m.get("usage")
        if isinstance(u, dict):
            yield m.get("model"), u


def _n(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def last_context_tokens(path):
    """Last assistant usage = what is actually in the window."""
    last = None
    for _model, u in _iter_assistant_usages(path):
        tok = (_n(u.get("input_tokens"))
               + _n(u.get("cache_read_input_tokens"))
               + _n(u.get("cache_creation_input_tokens")))
        if tok > 0:
            last = tok
    return last


def last_model(path):
    last = None
    for model, _u in _iter_assistant_usages(path):
        if isinstance(model, str) and model and model != "<synthetic>":
            last = model
    return last


def last_effort(path):
    """Reasoning effort of the most recent assistant turn.

    Claude Code records it as a top-level field on every assistant entry, so the
    effort is available even when no sensor runs — which is the normal case in
    an IDE, where no status line executes. Without this, line 1 silently lost
    its effort suffix on every cascade level below S1.
    """
    last = None
    for entry, _m in _iter_assistant_lines(path, '"effort"'):
        e = entry.get("effort")
        if isinstance(e, str) and e:
            last = e
    return last


def _price_for(model_id, prices):
    """Prices by model family. For the cost fallback only — the window size is
    NEVER derived from the name."""
    mid = (model_id or "").lower()
    for key in ("fable", "mythos", "haiku", "sonnet"):
        if key in mid:
            return prices.get(key if key != "mythos" else "fable", prices["default"])
    return prices["default"]


def session_cost(path, prices):
    """Estimated session cost (USD). Cache writes are weighted by TTL: a
    5-minute cache costs 1.25× the input price, a 1-hour cache 2.0×. Claude Code
    writes almost exclusively 1h cache — the earlier flat 1.25× noticeably
    understated the cost."""
    total, found = 0.0, False
    for model, u in _iter_assistant_usages(path):
        p = _price_for(model, prices)
        pin, pout = p["input"] / 1e6, p["output"] / 1e6
        cc = u.get("cache_creation") or {}
        h1 = _n(cc.get("ephemeral_1h_input_tokens"))
        h5 = _n(cc.get("ephemeral_5m_input_tokens"))
        # Older transcripts without the sub-object: count them all as 5m.
        if not h1 and not h5:
            h5 = _n(u.get("cache_creation_input_tokens"))
        total += (_n(u.get("input_tokens")) * pin
                  + _n(u.get("cache_read_input_tokens")) * pin * 0.1
                  + h5 * pin * 1.25
                  + h1 * pin * 2.0
                  + _n(u.get("output_tokens")) * pout)
        found = True
    return total if found else None


def git_ahead(cwd):
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
def _fmt_span(secs):
    if secs is None or secs <= 0:
        return None
    mins = secs / 60
    if mins < 60:
        return "%dm" % max(1, round(mins))
    hours = mins / 60
    if hours < 24:
        return "%dh" % round(hours)
    return "%dd" % round(hours / 24)


def usage_from_sensor(rate_limits, bands, segments, t):
    """The preferred path: the status line delivers rate_limits along with
    everything else — no OAuth, no keychain, no HTTP call inside the Stop hook."""
    if not rate_limits:
        return None
    now = time.time()
    parts = []
    for key, label, with_bar in (("five_hour", t("session"), True),
                                 ("seven_day", t("week"), False),
                                 ("seven_day_sonnet", t("sonnet_week"), False)):
        w = rate_limits.get(key)
        if not isinstance(w, dict) or not isinstance(w.get("pct"), (int, float)):
            continue
        p = int(round(w["pct"]))
        r = _fmt_span(w["resets_at"] - now) if isinstance(w.get("resets_at"), (int, float)) else None
        cd = " (↻%s)" % (r or t("reset_now"))
        if with_bar:
            parts.append("%s %s %d%%%s" % (label, gradient_bar(p, bands, segments), p, cd))
        else:
            parts.append("%s %d%%%s" % (label, p, cd))
    return "\U0001F4CA " + " · ".join(parts) if parts else None


def usage_from_api(bands, segments, t):
    """Fallback without a sensor: the original OAuth path."""
    try:
        u = get_usage()
    except Exception:
        u = None
    if not u:
        return None
    parts = []
    for key, label, with_bar in (("five_hour", t("session"), True),
                                 ("seven_day", t("week"), False),
                                 ("seven_day_sonnet", t("sonnet_week"), False)):
        w = u.get(key)
        if not isinstance(w, dict) or not isinstance(w.get("pct"), (int, float)):
            continue
        p = int(round(w["pct"]))
        r = fmt_reset(w.get("resets_at", ""), t("reset_now"))
        cd = " (↻%s)" % r if r else ""
        if with_bar:
            parts.append("%s %s %d%%%s" % (label, gradient_bar(p, bands, segments), p, cd))
        else:
            parts.append("%s %d%%%s" % (label, p, cd))
    return "\U0001F4CA " + " · ".join(parts) if parts else None


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
# Building the block
# ---------------------------------------------------------------------------
def model_line(ctx, t):
    """Line 1: which model is running, with which window.

    The model name may come from the transcript — it is correct there, just
    without any window information. The window size is appended only when it has
    been measured or declared. The effort comes from the sensor when there is
    one, and from the transcript otherwise.
    """
    name = ctx.model or t("model_unknown")
    bits = ["\U0001F9E0 %s" % name]                                    # 🧠
    if ctx.known:
        bits.append("%s %s" % (fmt_window(ctx.window), t("window_word")))
    if ctx.effort:
        bits.append("effort %s" % ctx.effort)
    return " · ".join(bits)


def context_line(ctx, cfg, t, cost, ahead):
    """Line 2: context load. With a measured window as a percentage bar, without
    one as a plain number — no percentages, no colour, no alarm."""
    bands, segments = cfg["bands"], cfg["segments"]

    if ctx.known:
        pct = ctx.pct or 0
        emoji, _sound, hint = tier(pct, bands, t)
        # "*" marks a declared (not measured) window. A stale sensor gets no
        # marker: the window size is measured in that case too, and it does not
        # change within a session.
        mark = "*" if ctx.confidence == "declared" else ""
        line = "%s %s %s %d%% · %s/%s%s" % (
            emoji, t("context"), gradient_bar(pct, bands, segments), pct,
            fmt_tokens(ctx.tokens), fmt_window(ctx.window), mark)
    else:
        tpl = "ctx_unknown_floor" if ctx.floor else "ctx_unknown"
        body = t(tpl).format(tokens=fmt_tokens(ctx.tokens),
                             floor=fmt_window(ctx.floor))
        line = "⚪ %s %s" % (t("context"), body)                   # ⚪
        hint = t("hint_no_sensor")

    if cost is not None:
        line += " · \U0001F4B0 $%.2f" % cost
    if ahead:
        line += " · ⇡%d %s" % (ahead, t("unpushed"))
    return line, hint


def build_block(ctx, cfg, t, cost, ahead, usage_line):
    lines = []
    if cfg.get("features", {}).get("model_line", True):
        lines.append(model_line(ctx, t))
    line, hint = context_line(ctx, cfg, t, cost, ahead)
    lines.append(line)
    if usage_line:
        lines.append(usage_line)
    lines.append("\U0001F4A1 %s" % hint)                               # 💡
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    # Loop guard: after our own decision:block the hook fires again.
    if ev.get("stop_hook_active"):
        return

    sid = ev.get("session_id") or "unknown"
    tpath = ev.get("transcript_path")
    cwd = ev.get("cwd") or ""

    cfg = load_config()
    clients = cfg.get("clients") or ["ide", "terminal"]
    if isinstance(clients, list) and current_client() not in clients:
        return
    t = translator(cfg.get("language"))
    feats = cfg.get("features", {})

    # Transcript values as a fallback (the sensor wins inside resolve()).
    tokens = model = effort = None
    if tpath and os.path.exists(tpath):
        tokens = last_context_tokens(tpath)
        if not tokens:
            time.sleep(0.4)          # the transcript lags the turn briefly
            tokens = last_context_tokens(tpath)
        model = last_model(tpath)
        effort = last_effort(tpath)

    ctx = resolve(sid, cfg=cfg, transcript_tokens=tokens, transcript_model=model,
                  transcript_effort=effort)

    if not ctx.tokens:
        return
    if ctx.tokens < cfg.get("display_min_tokens", 6000):
        return

    # Sound only with a known window, and only when moving up into a new band.
    if ctx.known and feats.get("sound", True):
        b = band_index(ctx.pct or 0, cfg["bands"])
        os.makedirs(STATE_DIR, exist_ok=True)
        statef = os.path.join(STATE_DIR, sid + ".band")
        prev = -1
        try:
            with open(statef) as f:
                prev = int(f.read().strip() or "-1")
        except Exception:
            pass
        try:
            with open(statef, "w") as f:
                f.write(str(b))
        except Exception:
            pass
        _e, sound_key, _h = tier(ctx.pct or 0, cfg["bands"], t)
        if b > prev and sound_key:
            play(cfg.get("sounds", {}).get(sound_key))

    cost = None
    if feats.get("cost", True):
        cost = ctx.cost
        if cost is None and tpath and os.path.exists(tpath):
            cost = session_cost(tpath, cfg["prices_per_mtok"])

    ahead = git_ahead(cwd) if feats.get("git_ahead", True) else None

    usage_line = None
    if feats.get("usage", True):
        usage_line = usage_from_sensor(ctx.rate_limits, cfg["bands"], cfg["segments"], t)
        if not usage_line:
            usage_line = usage_from_api(cfg["bands"], cfg["segments"], t)

    block = build_block(ctx, cfg, t, cost, ahead, usage_line)
    if resolve_output_mode(cfg) == "block":
        out = {"decision": "block", "reason": t("instruction").format(block=block)}
    else:
        out = {"systemMessage": block}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
