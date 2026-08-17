#!/usr/bin/env python3
"""claude-context-meter — doctor: makes a misconfiguration visible.

Usage:  python3 src/doctor.py [session_id]

This project's predecessor showed wrong numbers for two months with nothing to
hint at it: window detection was silently falling back to 200k. That is exactly
what `doctor` checks — it answers "which level of the cascade is active right
now, and why not the one above it?"
"""
import sys, os, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import (diagnose, read_sensor, sensor_age, sensor_ever_wrote,  # noqa: E402
                     STATE_DIR, FRESH_SECS)

HOME = os.path.expanduser("~")
SETTINGS = [os.path.join(HOME, ".claude", "settings.json"),
            os.path.join(HOME, ".claude", "settings.local.json")]

OK, WARN, BAD = "  ok  ", " warn ", " FAIL "


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# Match on FILE NAMES, not loose substrings: an unrelated hook such as
# `load_vault_context.py` contains "context" and would otherwise count as ours.
SCRIPTS = {"statusline": "sensor.py", "stop": "context_meter.py", "start": "session_start.py"}


def check_registration():
    """Are the status line and the hooks present in a settings.json?"""
    rows = []
    statusline = stop = start = None
    for path in SETTINGS:
        d = _load(path)
        if not d:
            continue
        sl = d.get("statusLine")
        cmd = sl.get("command", "") if isinstance(sl, dict) else ""
        # The wrapper counts too: it calls the sensor.
        if SCRIPTS["statusline"] in cmd or "context-meter" in cmd:
            statusline = statusline or (cmd, path)
        for event, slot in (("Stop", "stop"), ("SessionStart", "start")):
            for group in d.get("hooks", {}).get(event, []) or []:
                for h in group.get("hooks", []) or []:
                    hcmd = h.get("command", "")
                    if SCRIPTS[slot] not in hcmd:
                        continue
                    if slot == "stop":
                        stop = stop or (hcmd, path)
                    else:
                        start = start or (hcmd, path)

    rows.append(("Status line (sensor)", statusline, True))
    rows.append(("Stop hook (dashboard)", stop, True))
    rows.append(("SessionStart hook (model line)", start, False))

    out = []
    for label, hit, required in rows:
        if hit:
            out.append("[%s] %-32s %s" % (OK, label, hit[0]))
        else:
            out.append("[%s] %-32s not registered in settings.json"
                       % (BAD if required else WARN, label))
    return out, bool(statusline)


def never_wrote():
    """Has the sensor ever written at all? If not, the status line has not run
    since it was registered — the normal state right after an install, not a
    defect. If readings are there but not the one being looked for, that is a
    real failure and is reported as one. Cache files in the same directory do
    not count as readings."""
    return not sensor_ever_wrote()


def check_sensor(sid):
    out = []
    sensor = read_sensor(sid, allow_last=True)
    if not sensor:
        if never_wrote():
            out.append("[%s] %-32s none yet — the status line has not written "
                       "since it was registered"
                       % (WARN, "Sensor data"))
        else:
            out.append("[%s] %-32s no file in %s" % (BAD, "Sensor data", STATE_DIR))
        return out, None
    age = sensor_age(sensor)
    fresh = age is not None and age <= FRESH_SECS
    out.append("[%s] %-32s %.0f s old%s"
               % (OK if fresh else WARN, "Sensor data", age or 0,
                  "" if fresh else " (older than %d s)" % FRESH_SECS))
    if not sensor.get("window"):
        out.append("[%s] %-32s status-line input carried no context_window_size"
                   % (BAD, "Window size"))
    else:
        out.append("[%s] %-32s %s tokens" % (OK, "Window size", "{:,}".format(sensor["window"])))
    return out, sensor


def active_level(sid, sensor, model_id=None):
    own = sensor and ((not sid) or sensor.get("session_id") == sid)
    if sensor and sensor.get("window") and own:
        age = sensor_age(sensor)
        if age is not None and age <= FRESH_SECS:
            return "S1  measured — from the status line, fresh"
        return "S2  measured_stale — measured, older (the window stays valid)"
    if os.environ.get("CONTEXT_METER_WINDOW"):
        return "S3  declared — from CONTEXT_METER_WINDOW"
    cfg = _load(os.path.join(HOME, ".claude", "context-meter", "config.json")) or {}
    if cfg.get("window_override"):
        return "S3  declared — from window_override in config.json"
    if model_id:
        try:
            from models_api import max_input_tokens
            from client_rules import effective_window
            cap = max_input_tokens(model_id)
            win, rule = effective_window(model_id, cap)
            if win:
                return ("S4  resolved — Models API (%s → max_input_tokens %s), rule %s"
                        % (model_id, "{:,}".format(cap) if cap else "?", rule))
        except Exception as e:
            return "S5  unknown — resolution failed (%s)" % e
    return "S5  unknown — no percentage, no alarm (by design)"


def check_rules(model_id):
    """Expose the inputs to Claude Code's own window rule."""
    from client_rules import (is_first_party, one_m_disabled, third_party_backend,
                              has_1m_suffix, effective_window)
    from models_api import max_input_tokens, get_token
    out = []
    out.append("[%s] %-32s %s" % (OK if get_token() else WARN, "OAuth token (Models API)",
                                  "found in the keychain" if get_token() else "missing → no API lookup"))
    out.append("[%s] %-32s %s" % (OK if is_first_party() else WARN, "First party (api.anthropic.com)",
                                  is_first_party()))
    if one_m_disabled():
        out.append("[%s] %-32s set → the window is capped at 200k"
                   % (WARN, "CLAUDE_CODE_DISABLE_1M_CONTEXT"))
    if third_party_backend():
        out.append("[%s] %-32s %s" % (WARN, "Third-party backend", third_party_backend()))
    if model_id:
        cap = max_input_tokens(model_id)
        win, rule = effective_window(model_id, cap)
        out.append("[%s] %-32s %s" % (OK if cap else BAD, "Model capacity (API)",
                                      "{:,}".format(cap) if cap else "unknown"))
        out.append("[%s] %-32s %s  (rule: %s)" % (OK if win else BAD, "Effective window",
                                                  "{:,}".format(win) if win else "indeterminable", rule))
        if has_1m_suffix(model_id):
            out.append("       (the model id carries the [1m] marker)")
    return out


def find_model(sid):
    """The model id last used in this session's transcript."""
    if not sid:
        return None
    import glob
    for path in glob.glob(os.path.join(HOME, ".claude", "projects", "*", sid + ".jsonl")):
        last = None
        try:
            with open(path, "rb") as f:
                for line in f.read().decode("utf-8", "replace").splitlines():
                    if '"model"' not in line:
                        continue
                    try:
                        m = (json.loads(line).get("message") or {})
                    except Exception:
                        continue
                    mid = m.get("model")
                    if m.get("role") == "assistant" and isinstance(mid, str) and mid != "<synthetic>":
                        last = mid
        except Exception:
            pass
        if last:
            return last
    return None


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_CODE_SESSION_ID")
    print("claude-context-meter · doctor")
    print("=" * 62)
    print("Session: %s\n" % (sid or "— none given, checking last.json"))

    reg, has_statusline = check_registration()
    for line in reg:
        print(line)
    print()

    sen, sensor = check_sensor(sid)
    for line in sen:
        print(line)
    print()

    model_id = (sensor or {}).get("model_id") or find_model(sid)
    print("Window resolution without a sensor (level S4):")
    print("   Model id from the transcript: %s" % (model_id or "— not found"))
    for line in check_rules(model_id):
        print(line)
    print()

    print("Active cascade level:")
    print("   %s" % active_level(sid, sensor, model_id))
    print()

    if not has_statusline:
        print("→ Without a registered status line the window size cannot be measured.")
        print("  It is the only place Claude Code hands out context_window_size.")
        print("  install.sh sets it up.")
    elif sensor and not sensor.get("window"):
        print("→ The status line runs but delivers no context_window_size.")
        print("  Update Claude Code, or set window_override in config.json")
        print("  (level S3).")
    elif not sensor:
        resolved = model_id and "resolved" in active_level(sid, sensor, model_id)
        if resolved:
            print("→ No sensor, but the window is established via level S4 — the reading")
            print("  is correct. The sensor would still be better: it carries Claude")
            print("  Code's own token count and cost instead of the ones reconstructed")
            print("  from the transcript. If the status line never runs, a client restart")
            print("  usually helps — the registration is read at startup.")
        elif never_wrote():
            print("→ Freshly set up: the status line is registered but has not written")
            print("  yet. That is normal right after an install — Claude Code reads the")
            print("  registration at startup. Restart the client, then send a message;")
            print("  after that a file appears in")
            print("  %s and the reading moves to S1." % STATE_DIR)
        else:
            print("→ The status line is registered but never wrote, and the resolution")
            print("  does not apply either. Check whether the client runs the status")
            print("  line at all: after an assistant reply there should be a file in")
            print("  %s." % STATE_DIR)

    if sensor:
        print()
        print("Last measurement:")
        for k in ("model_name", "model_id", "window", "tokens_in", "used_pct",
                  "cost_usd", "effort", "version"):
            if sensor.get(k) is not None:
                print("   %-12s %s" % (k, sensor[k]))


if __name__ == "__main__":
    main()
