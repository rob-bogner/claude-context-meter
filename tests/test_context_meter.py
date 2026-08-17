#!/usr/bin/env python3
"""Unit tests for claude-context-meter core logic (no network, no Keychain).

Run directly:   python3 tests/test_context_meter.py
Or with pytest: pytest -q
"""
import os
import sys
import json
import time
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import context_meter as cm  # noqa: E402
import context as cx  # noqa: E402
from i18n import translator  # noqa: E402

BANDS = [15, 30, 45]
# Prices are per model family, with a mandatory "default" fallback.
PRICES = {"default": {"input": 5.0, "output": 25.0},
          "sonnet": {"input": 3.0, "output": 15.0}}

# No model-name table anywhere: the window is measured, declared or resolved.
# Tests must never reach the network — `use_models_api: False` keeps S4 offline.
OFFLINE = {"bands": BANDS, "segments": 20, "use_models_api": False}


def _transcript(model="claude-opus-4-8", tokens=(2, 30000, 5000), effort=None):
    """Write a minimal JSONL transcript with one assistant usage line."""
    inp, cr, cw = tokens
    line = {
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": inp,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cw,
                "output_tokens": 100,
            },
        }
    }
    if effort:
        # Claude Code records the effort as a TOP-LEVEL field, beside "message".
        line["effort"] = effort
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(line) + "\n")
    return path


def check(name, cond):
    print(("  ok " if cond else "FAIL ") + name)
    assert cond, name


def test_gradient_bar():
    bar = cm.gradient_bar(50, BANDS, 20)
    check("bar length = 20 cells", len(bar) == 20)
    check("bar starts green", bar.startswith("\U0001F7E9"))
    check("empty bar all black", cm.gradient_bar(0, BANDS, 20) == "⬛" * 20)


def test_tier():
    t = translator("en")
    check("green tier", cm.tier(5, BANDS, t)[0] == "\U0001F7E2")
    check("yellow tier", cm.tier(20, BANDS, t)[0] == "\U0001F7E1")
    check("orange tier", cm.tier(35, BANDS, t)[0] == "\U0001F7E0")
    check("red tier", cm.tier(60, BANDS, t)[0] == "\U0001F534")
    check("red hint text (en)", cm.tier(60, BANDS, t)[2] == "Start a handoff / new session now")


def test_transcript_reads():
    p = _transcript(tokens=(2, 30000, 5000))
    try:
        check("last_context_tokens sums input+cache", cm.last_context_tokens(p) == 35002)
        check("last_model reads id", cm.last_model(p) == "claude-opus-4-8")
    finally:
        os.remove(p)


def _sensor(state_dir, session_id="sess", window=1_000_000, tokens=120_000, age=0.0,
            **extra):
    """Write a sensor reading the way src/sensor.py would."""
    data = {"schema": 1, "session_id": session_id, "window": window,
            "tokens_in": tokens, "ts": time.time() - age}
    data.update(extra)
    with open(os.path.join(state_dir, session_id + ".json"), "w") as f:
        json.dump(data, f)
    return data


class _State(object):
    """Point context.STATE_DIR at a scratch directory for the duration."""

    def __enter__(self):
        self._orig = cx.STATE_DIR
        self.dir = tempfile.mkdtemp()
        cx.STATE_DIR = self.dir
        return self.dir

    def __exit__(self, *exc):
        cx.STATE_DIR = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def test_cascade_s1_measured():
    """A fresh sensor reading is the measurement — nothing may override it."""
    with _State() as d:
        _sensor(d, window=1_000_000, tokens=120_000, age=0)
        ctx = cx.resolve("sess", OFFLINE, transcript_tokens=999, transcript_model="x")
        check("S1 source is the status line", ctx.source == "statusline")
        check("S1 confidence measured", ctx.confidence == "measured")
        check("S1 window from sensor", ctx.window == 1_000_000)
        check("S1 sensor tokens beat the transcript", ctx.tokens == 120_000)
        check("S1 percentage", ctx.pct == 12)


def test_cascade_s2_stale_keeps_window():
    """A window cannot change mid-session, so a stale reading still carries it —
    but the token count is taken from the transcript."""
    with _State() as d:
        _sensor(d, window=1_000_000, tokens=120_000, age=10_000)
        ctx = cx.resolve("sess", OFFLINE, transcript_tokens=300_000, transcript_model="x")
        check("S2 window survives", ctx.window == 1_000_000)
        check("S2 marked stale", ctx.confidence == "measured_stale")
        check("S2 tokens follow the transcript", ctx.tokens == 300_000)
        check("S2 still counts as known", ctx.known)


def test_cascade_sensor_of_foreign_session_ignored():
    with _State() as d:
        _sensor(d, session_id="other", window=1_000_000)
        ctx = cx.resolve("mine", OFFLINE, transcript_tokens=50_000, transcript_model=None)
        check("foreign sensor is not used", ctx.source != "statusline")


def test_cascade_s3_declared():
    with _State():
        cfg = dict(OFFLINE, window_override=1_000_000)
        ctx = cx.resolve("nosession", cfg, transcript_tokens=120_000, transcript_model="x")
        check("S3 source is the override", ctx.source == "override")
        check("S3 confidence declared", ctx.confidence == "declared")
        check("S3 window honoured", ctx.window == 1_000_000)


def test_cascade_s5_unknown_never_alarms():
    """The regression that produced '100% · 201k/200k': with no verifiable
    window, the meter must show a token count and no percentage at all."""
    with _State():
        ctx = cx.resolve("nosession", OFFLINE, transcript_tokens=201_000,
                         transcript_model=None)
        check("S5 confidence unknown", ctx.confidence == "unknown")
        check("S5 is not 'known'", not ctx.known)
        check("S5 has no percentage", ctx.pct is None)
        line, hint = cm.context_line(ctx, OFFLINE, translator("en"), None, 0)
        check("S5 line shows no percent sign", "%" not in line)
        check("S5 reports the tokens", "201k" in line)


def test_effort_comes_from_the_transcript_without_a_sensor():
    """In an IDE no status line runs, so the sensor never writes and every level
    below S1 used to drop the effort from line 1. It is a top-level field on
    each assistant entry, so it is available regardless."""
    p = _transcript(model="claude-opus-5", tokens=(2, 30000, 5000), effort="xhigh")
    try:
        check("last_effort reads the top-level field", cm.last_effort(p) == "xhigh")
    finally:
        os.remove(p)
    p = _transcript(model="claude-opus-5", tokens=(2, 30000, 5000))
    try:
        check("no effort in the transcript, no guess", cm.last_effort(p) is None)
    finally:
        os.remove(p)


def test_effort_survives_every_cascade_level():
    t = translator("en")
    with _State() as d:
        # S1 — the sensor's own effort wins.
        _sensor(d, effort="high")
        ctx = cx.resolve("sess", OFFLINE, transcript_tokens=1, transcript_effort="xhigh")
        check("S1 prefers the sensor's effort", ctx.effort == "high")
        # S1 — sensor without an effort field falls back to the transcript.
        _sensor(d)
        ctx = cx.resolve("sess", OFFLINE, transcript_tokens=1, transcript_effort="xhigh")
        check("S1 falls back to the transcript", ctx.effort == "xhigh")
    with _State():
        cfg = dict(OFFLINE, window_override=1_000_000)
        ctx = cx.resolve("none", cfg, transcript_tokens=120_000, transcript_effort="xhigh")
        check("S3 keeps the effort", ctx.effort == "xhigh")
        check("S3 renders it", "effort xhigh" in cm.model_line(ctx, t))
    with _State():
        ctx = cx.resolve("none", OFFLINE, transcript_tokens=201_000, transcript_effort="xhigh")
        check("S5 keeps the effort", ctx.effort == "xhigh")
        check("S5 renders it", "effort xhigh" in cm.model_line(ctx, t))


def test_cache_files_are_not_sensor_readings():
    """models.json and usage-cache.json live in the same directory. Counting
    them as readings made a fresh install look like a broken one."""
    with _State() as d:
        check("empty state: never wrote", not cx.sensor_ever_wrote())
        with open(os.path.join(d, "models.json"), "w") as f:
            json.dump({"claude-x": {"ts": 0}}, f)
        with open(os.path.join(d, "usage-cache.json"), "w") as f:
            json.dump({"at": 0}, f)
        check("caches only: still never wrote", not cx.sensor_ever_wrote())
        _sensor(d)
        check("a real reading counts", cx.sensor_ever_wrote())


def test_floor_is_a_lower_bound_only():
    check("30k fits in 200k", cx.floor_for(30_000) == 200_000)
    check("201k lifts to 1M", cx.floor_for(201_000) == 1_000_000)
    check("beyond the tiers, tokens are the bound", cx.floor_for(3_000_000) == 3_000_000)
    check("no tokens, no bound", cx.floor_for(0) is None)


def test_build_block_en_de():
    ctx = cx.Ctx(window=1_000_000, tokens=120_000, source="statusline",
                 confidence="measured", model="Opus 5")
    en = cm.build_block(ctx, OFFLINE, translator("en"), 0.42, 3, None)
    check("en says Context", "Context" in en)
    check("en shows /1M", "/1M" in en)
    check("en unpushed label", "unpushed" in en)
    de = cm.build_block(ctx, OFFLINE, translator("de"), 0.42, 3, None)
    check("de says Kontext", "Kontext" in de)
    check("de unpushed label", "ungepusht" in de)

    cfg_no_model = dict(OFFLINE, features={"model_line": False})
    plain = cm.build_block(ctx, cfg_no_model, translator("en"), 0.42, 3, None)
    check("model line can be switched off", not plain.splitlines()[0].startswith("\U0001F9E0"))
    check("context line is then first", "Context" in plain.splitlines()[0])


def test_cost():
    p = _transcript(tokens=(1000, 0, 0))
    try:
        c = cm.session_cost(p, PRICES)
        # 1000 input * $5/Mtok + 100 output * $25/Mtok = 0.005 + 0.0025
        check("cost math", abs(c - 0.0075) < 1e-9)
    finally:
        os.remove(p)


def test_cost_picks_the_model_family():
    """Prices are per family, matched on the id — the one place a model name is
    read at all, and only ever for money, never for the window."""
    p = _transcript(model="claude-sonnet-5", tokens=(1000, 0, 0))
    try:
        c = cm.session_cost(p, PRICES)
        # sonnet: 1000 * $3/Mtok + 100 * $15/Mtok
        check("sonnet is billed at sonnet rates", abs(c - (0.003 + 0.0015)) < 1e-9)
    finally:
        os.remove(p)
    p = _transcript(model="claude-something-new-9", tokens=(1000, 0, 0))
    try:
        c = cm.session_cost(p, PRICES)
        check("an unknown family falls back to default", abs(c - 0.0075) < 1e-9)
    finally:
        os.remove(p)


def test_cost_weights_cache_writes_by_ttl():
    """A 1-hour cache write costs 2x input, a 5-minute one 1.25x, and a cache
    read a tenth. Claude Code writes almost exclusively 1h."""
    def _usage(**cache):
        line = {"message": {"role": "assistant", "model": "claude-opus-5",
                            "usage": dict({"input_tokens": 0, "output_tokens": 0},
                                          **cache)}}
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(line) + "\n")
        return path

    p = _usage(cache_creation={"ephemeral_1h_input_tokens": 1_000_000})
    try:
        check("1h cache write = 2x input", abs(cm.session_cost(p, PRICES) - 10.0) < 1e-9)
    finally:
        os.remove(p)
    p = _usage(cache_creation={"ephemeral_5m_input_tokens": 1_000_000})
    try:
        check("5m cache write = 1.25x input", abs(cm.session_cost(p, PRICES) - 6.25) < 1e-9)
    finally:
        os.remove(p)
    p = _usage(cache_read_input_tokens=1_000_000)
    try:
        check("cache read = 0.1x input", abs(cm.session_cost(p, PRICES) - 0.5) < 1e-9)
    finally:
        os.remove(p)
    p = _usage(cache_creation_input_tokens=1_000_000)
    try:
        check("legacy field counts as 5m", abs(cm.session_cost(p, PRICES) - 6.25) < 1e-9)
    finally:
        os.remove(p)


def test_client_and_output_mode():
    old = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
    try:
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "claude-vscode"
        check("vscode -> ide", cm.current_client() == "ide")
        check("auto+ide -> block", cm.resolve_output_mode({"output_mode": "auto"}) == "block")
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        check("cli -> terminal", cm.current_client() == "terminal")
        check("auto+terminal -> system", cm.resolve_output_mode({"output_mode": "auto"}) == "system")
        check("explicit block wins", cm.resolve_output_mode({"output_mode": "block"}) == "block")
        check("explicit system wins", cm.resolve_output_mode({"output_mode": "system"}) == "system")
    finally:
        if old is None:
            os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)
        else:
            os.environ["CLAUDE_CODE_ENTRYPOINT"] = old


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        print(fn.__name__)
        fn()
    print("\nAll %d test groups passed." % len(fns))


if __name__ == "__main__":
    run_all()
