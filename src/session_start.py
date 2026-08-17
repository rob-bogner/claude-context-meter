#!/usr/bin/env python3
"""claude-context-meter — SessionStart-Hook: zeigt Modell und Fenster beim Start.

Ausgabe als `systemMessage`, die Claude Code dem Nutzer anzeigt:

    🧠 Session läuft auf Opus 5 · 1M Fenster

Woher der Wert kommt, in dieser Reihenfolge:

  1. Das optionale `model`-Feld des SessionStart-Events. Laut Doku ist
     SessionStart das EINZIGE Event, das ein Modell mitliefern kann — garantiert
     ist es nicht, deshalb nur als erster Versuch.
  2. Der eigene Sensor. Die Status-Line läuft ebenfalls beim Session-Start; ob
     vor oder nach diesem Hook, ist nicht festgelegt, daher wird kurz gepollt.
  3. Der Sensor der zuletzt aktiven Session (`last.json`) — klar als Vorschau
     gekennzeichnet, weil er ein anderes Modell zeigen kann.

Fehlt alles, bleibt der Hook still. Ein SessionStart-Hook, der bei jedem Start
eine Vermutung ausgibt, wäre genau der Fehler, den dieses Projekt behebt.
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

POLL_SECS = 1.2     # wie lange auf den frischen Sensor gewartet wird
POLL_STEP = 0.1
LAST_MAX_AGE = 3600  # `last.json` älter als eine Stunde ist keine Vorschau wert


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


def _describe(name, window, effort, t):
    bits = [name]
    wl = _fmt_window(window)
    if wl:
        bits.append("%s %s" % (wl, t("window_word")))
    if effort:
        bits.append("effort %s" % effort)
    return " · ".join(bits)


def wait_for_sensor(sid):
    """Kurz auf den Sensor der eigenen Session warten."""
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
    t = translator(_lang())

    # 1 — Modell direkt aus dem Event (nicht garantiert vorhanden)
    ev_model = ev.get("model")
    if isinstance(ev_model, dict):
        ev_model = ev_model.get("display_name") or ev_model.get("id")

    # 2 — eigener Sensor
    sensor = wait_for_sensor(sid) if sid else None
    if sensor:
        name = sensor.get("model_name") or sensor.get("model_id") or ev_model
        if name:
            msg = t("session_start").format(
                model=_describe(name, sensor.get("window"), sensor.get("effort"), t))
            print(json.dumps({"systemMessage": "\U0001F9E0 " + msg}))
            return

    # 2b — Modell bekannt, Fenster über Models API + Client-Regeln herleiten.
    # Damit steht die Zeile auch dann vollständig da, wenn die Status-Line beim
    # Start noch nicht gelaufen ist.
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
        msg = t("session_start").format(model=_describe(ev_model, window, None, t))
        print(json.dumps({"systemMessage": "\U0001F9E0 " + msg}))
        return

    # 3 — Vorschau aus der letzten Session, klar als solche gekennzeichnet
    last = read_sensor(None, allow_last=True)
    if last and last.get("session_id") != sid:
        age = sensor_age(last)
        name = last.get("model_name") or last.get("model_id")
        if name and (age is None or age <= LAST_MAX_AGE):
            msg = t("session_start_last").format(
                model=_describe(name, last.get("window"), None, t))
            print(json.dumps({"systemMessage": "\U0001F9E0 " + msg}))
            return

    # Nichts Belastbares — dann lieber schweigen.


if __name__ == "__main__":
    main()
