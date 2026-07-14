#!/usr/bin/env python3
"""Unit tests for claude-context-meter core logic (no network, no Keychain).

Run directly:   python3 tests/test_context_meter.py
Or with pytest: pytest -q
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import context_meter as cm  # noqa: E402
from i18n import translator  # noqa: E402

BANDS = [15, 30, 45]
MODEL_WINDOWS = {"opus-4-8": 1000000, "fable-5": 1000000, "sonnet": 200000, "haiku": 200000}
PRICES = {"input": 5.0, "cache_write": 6.25, "cache_read": 0.5, "output": 25.0}


def _transcript(model="claude-opus-4-8", tokens=(2, 30000, 5000)):
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


def test_read_window_model_mapping():
    p = _transcript(model="claude-opus-4-8", tokens=(2, 30000, 5000))
    try:
        w = cm.read_window("nosession", p, MODEL_WINDOWS, 35002)
        check("opus-4-8 -> 1M", w == 1_000_000)
    finally:
        os.remove(p)
    p = _transcript(model="claude-sonnet-5", tokens=(2, 30000, 5000))
    try:
        w = cm.read_window("nosession", p, MODEL_WINDOWS, 35002)
        check("sonnet -> 200k", w == 200_000)
    finally:
        os.remove(p)


def test_read_window_empirical_net():
    # Unknown model, but observed tokens exceed 200k -> must lift to 1M.
    p = _transcript(model="claude-unknown-9", tokens=(2, 250000, 5000))
    try:
        w = cm.read_window("nosession", p, MODEL_WINDOWS, 255002)
        check("unknown model, >200k tokens -> 1M", w == 1_000_000)
    finally:
        os.remove(p)


def test_read_window_sensor_precedence(tmp_state=None):
    # Sensor file wins over model mapping.
    orig = cm.STATE_DIR
    d = tempfile.mkdtemp()
    cm.STATE_DIR = d
    try:
        with open(os.path.join(d, "sess.window"), "w") as f:
            f.write("200000")
        p = _transcript(model="claude-opus-4-8", tokens=(2, 30000, 5000))
        try:
            w = cm.read_window("sess", p, MODEL_WINDOWS, 35002)
            check("sensor file (200k) overrides opus mapping", w == 200_000)
        finally:
            os.remove(p)
    finally:
        cm.STATE_DIR = orig


def test_build_block_en_de():
    cfg = {"bands": BANDS, "segments": 20}
    en = cm.build_block(cfg, translator("en"), 120000, 1000000, 0.42, 3, None)
    check("en says Context", "Context" in en.splitlines()[0])
    check("en shows /1M", "/1M" in en)
    check("en unpushed label", "unpushed" in en)
    de = cm.build_block(cfg, translator("de"), 120000, 1000000, 0.42, 3, None)
    check("de says Kontext", "Kontext" in de.splitlines()[0])
    check("de unpushed label", "ungepusht" in de)


def test_cost():
    p = _transcript(tokens=(1000, 0, 0))
    try:
        c = cm.session_cost(p, PRICES)
        # 1000 input * $5/Mtok + 100 output * $25/Mtok = 0.005 + 0.0025
        check("cost math", abs(c - 0.0075) < 1e-9)
    finally:
        os.remove(p)


def run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        print(fn.__name__)
        fn()
    print("\nAll %d test groups passed." % len(fns))


if __name__ == "__main__":
    run_all()
