#!/usr/bin/env python3
"""claude-context-meter — SessionStart hook: shows model and window at startup.

Printed as a `systemMessage`, which Claude Code shows to the user:

    🧠 Session running on Opus 5 · 1M window

Where the value comes from, in this order:

  1. The optional `model` field of the SessionStart event. Per the docs,
     SessionStart is the ONLY event that can carry a model — it is not
     guaranteed, so this is only the first attempt.
  2. Our own sensor. The status line also runs at session start; whether before
     or after this hook is not defined, so it is polled briefly.
  3. The sensor of the most recently active session (`last.json`) — clearly
     marked as a preview, because it may show a different model.

If all of that is missing, the hook stays silent. A SessionStart hook that
prints a guess on every start would be exactly the mistake this project fixes.
"""
import sys, os, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import read_sensor, sensor_age  # noqa: E402

try:
    from i18n import translator
except Exception:                                   # pragma: no cover
    def translator(_lang):
        return lambda k: k

HOME = os.path.expanduser("~")
CONFIG_PATH = os.environ.get(
    "CONTEXT_METER_CONFIG", os.path.join(HOME, ".claude", "context-meter", "config.json")
)

POLL_SECS = 1.2     # how long to wait for a fresh sensor
POLL_STEP = 0.1
LAST_MAX_AGE = 3600  # a `last.json` older than an hour is not worth previewing


def _lang():
    if os.environ.get("CONTEXT_METER_LANG"):
        return os.environ["CONTEXT_METER_LANG"]
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f).get("language")
    except Exception:
        return None


def _fmt_window(w):
    if not w:
        return None
    return "%gM" % (w / 1_000_000) if w >= 1_000_000 else "%dk" % (w // 1000)


def _effort_from_transcript(path):
    """The transcript carries the effort on every assistant entry, so the line
    can show it even when the status line has not run yet."""
    if not path or not os.path.exists(path):
        return None
    try:
        from context_meter import last_effort
        return last_effort(path)
    except Exception:
        return None


def _describe(name, window, effort, t):
    bits = [name]
    wl = _fmt_window(window)
    if wl:
        bits.append("%s %s" % (wl, t("window_word")))
    if effort:
        bits.append("effort %s" % effort)
    return " · ".join(bits)


def wait_for_sensor(sid):
    """Wait briefly for this session's own sensor."""
    deadline = time.time() + POLL_SECS
    while time.time() < deadline:
        s = read_sensor(sid)
        if s and s.get("session_id") == sid and (s.get("model_name") or s.get("window")):
            return s
        time.sleep(POLL_STEP)
    return None


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return

    sid = ev.get("session_id")
    tpath = ev.get("transcript_path")
    t = translator(_lang())

    # 1 — model straight from the event (not guaranteed to be present)
    ev_model = ev.get("model")
    if isinstance(ev_model, dict):
        ev_model = ev_model.get("display_name") or ev_model.get("id")

    # 2 — our own sensor
    sensor = wait_for_sensor(sid) if sid else None
    if sensor:
        name = sensor.get("model_name") or sensor.get("model_id") or ev_model
        if name:
            effort = sensor.get("effort") or _effort_from_transcript(tpath)
            msg = t("session_start").format(
                model=_describe(name, sensor.get("window"), effort, t))
            print(json.dumps({"systemMessage": "\U0001F9E0 " + msg}))
            return

    # 2b — model known, window resolved via the Models API plus the client rules.
    # This keeps the line complete even when the status line has not run at
    # startup, which is the normal case in an IDE.
    if ev_model:
        window = None
        try:
            from models_api import max_input_tokens, display_name
            from client_rules import effective_window
            cap = max_input_tokens(ev_model)
            window, _rule = effective_window(ev_model, cap)
            ev_model = display_name(ev_model) or ev_model
        except Exception:
            pass
        msg = t("session_start").format(
            model=_describe(ev_model, window, _effort_from_transcript(tpath), t))
        print(json.dumps({"systemMessage": "\U0001F9E0 " + msg}))
        return

    # 3 — preview from the last session, clearly marked as such
    last = read_sensor(None, allow_last=True)
    if last and last.get("session_id") != sid:
        age = sensor_age(last)
        name = last.get("model_name") or last.get("model_id")
        if name and (age is None or age <= LAST_MAX_AGE):
            msg = t("session_start_last").format(
                model=_describe(name, last.get("window"), None, t))
            print(json.dumps({"systemMessage": "\U0001F9E0 " + msg}))
            return

    # Nothing solid — better to stay quiet.


if __name__ == "__main__":
    main()
