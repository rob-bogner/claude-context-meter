#!/usr/bin/env python3
"""claude-context-meter — doctor: macht Fehlkonfiguration sichtbar.

Aufruf:  python3 src/doctor.py [session_id]

Der Vorgänger dieses Projekts hat zwei Monate lang falsche Zahlen gezeigt, ohne
dass irgendetwas darauf hingewiesen hätte: die Fenstererkennung fiel still in
einen 200k-Fallback. Genau das prüft `doctor` — es beantwortet die Frage
„welche Ebene der Kaskade greift gerade, und warum nicht die darüber?"
"""
import sys, os, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context import diagnose, read_sensor, sensor_age, STATE_DIR, FRESH_SECS  # noqa: E402

HOME = os.path.expanduser("~")
SETTINGS = [os.path.join(HOME, ".claude", "settings.json"),
            os.path.join(HOME, ".claude", "settings.local.json")]

OK, WARN, BAD = "  ok  ", " warn ", " FEHLT"


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# Auf DATEINAMEN prüfen, nicht auf lose Teilwörter: ein fremder Hook wie
# `load_vault_context.py` enthält "context" und würde sonst als unserer gelten.
SCRIPTS = {"statusline": "sensor.py", "stop": "context_meter.py", "start": "session_start.py"}


def check_registration():
    """Sind Status-Line und Hooks in einer settings.json eingetragen?"""
    rows = []
    statusline = stop = start = None
    for path in SETTINGS:
        d = _load(path)
        if not d:
            continue
        sl = d.get("statusLine")
        cmd = sl.get("command", "") if isinstance(sl, dict) else ""
        # Auch der Wrapper zählt: er ruft den Sensor auf.
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

    rows.append(("Status-Line (Sensor)", statusline, True))
    rows.append(("Stop-Hook (Dashboard)", stop, True))
    rows.append(("SessionStart-Hook (Modellzeile)", start, False))

    out = []
    for label, hit, required in rows:
        if hit:
            out.append("[%s] %-32s %s" % (OK, label, hit[0]))
        else:
            out.append("[%s] %-32s nicht in settings.json registriert"
                       % (BAD if required else WARN, label))
    return out, bool(statusline)


def check_sensor(sid):
    out = []
    sensor = read_sensor(sid, allow_last=True)
    if not sensor:
        out.append("[%s] %-32s keine Datei in %s" % (BAD, "Sensordaten", STATE_DIR))
        return out, None
    age = sensor_age(sensor)
    fresh = age is not None and age <= FRESH_SECS
    out.append("[%s] %-32s %.0f s alt%s"
               % (OK if fresh else WARN, "Sensordaten", age or 0,
                  "" if fresh else " (älter als %d s)" % FRESH_SECS))
    if not sensor.get("window"):
        out.append("[%s] %-32s Status-Line-Input enthielt kein context_window_size"
                   % (BAD, "Fenstergröße"))
    else:
        out.append("[%s] %-32s %s Token" % (OK, "Fenstergröße", "{:,}".format(sensor["window"])))
    return out, sensor


def active_level(sid, sensor, model_id=None):
    own = sensor and ((not sid) or sensor.get("session_id") == sid)
    if sensor and sensor.get("window") and own:
        age = sensor_age(sensor)
        if age is not None and age <= FRESH_SECS:
            return "S1  measured — gemessen (Status-Line), frisch"
        return "S2  measured_stale — gemessen, älter (Fenster bleibt gültig)"
    if os.environ.get("CONTEXT_METER_WINDOW"):
        return "S3  declared — aus CONTEXT_METER_WINDOW"
    cfg = _load(os.path.join(HOME, ".claude", "context-meter", "config.json")) or {}
    if cfg.get("window_override"):
        return "S3  declared — aus config.json window_override"
    if model_id:
        try:
            from models_api import max_input_tokens
            from client_rules import effective_window
            cap = max_input_tokens(model_id)
            win, rule = effective_window(model_id, cap)
            if win:
                return ("S4  resolved — Models API (%s → max_input_tokens %s), Regel %s"
                        % (model_id, "{:,}".format(cap) if cap else "?", rule))
        except Exception as e:
            return "S5  unknown — Herleitung fehlgeschlagen (%s)" % e
    return "S5  unknown — keine Prozentanzeige, kein Alarm (so gewollt)"


def check_rules(model_id):
    """Die Eingaben von Claude Codes Fensterregel sichtbar machen."""
    from client_rules import (is_first_party, one_m_disabled, third_party_backend,
                              has_1m_suffix, effective_window)
    from models_api import max_input_tokens, get_token
    out = []
    out.append("[%s] %-32s %s" % (OK if get_token() else WARN, "OAuth-Token (Models API)",
                                  "im Keychain gefunden" if get_token() else "fehlt → keine API-Abfrage"))
    out.append("[%s] %-32s %s" % (OK if is_first_party() else WARN, "First Party (api.anthropic.com)",
                                  is_first_party()))
    if one_m_disabled():
        out.append("[%s] %-32s gesetzt → Fenster wird auf 200k begrenzt"
                   % (WARN, "CLAUDE_CODE_DISABLE_1M_CONTEXT"))
    if third_party_backend():
        out.append("[%s] %-32s %s" % (WARN, "Fremd-Backend", third_party_backend()))
    if model_id:
        cap = max_input_tokens(model_id)
        win, rule = effective_window(model_id, cap)
        out.append("[%s] %-32s %s" % (OK if cap else BAD, "Modellkapazität (API)",
                                      "{:,}".format(cap) if cap else "unbekannt"))
        out.append("[%s] %-32s %s  (Regel: %s)" % (OK if win else BAD, "Effektives Fenster",
                                                   "{:,}".format(win) if win else "unbestimmbar", rule))
        if has_1m_suffix(model_id):
            out.append("       (Modell-ID trägt [1m]-Kennung)")
    return out


def find_model(sid):
    """Zuletzt verwendete Modell-ID aus dem Transcript der Session."""
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
    print("Session: %s\n" % (sid or "— keine angegeben, prüfe last.json"))

    reg, has_statusline = check_registration()
    for line in reg:
        print(line)
    print()

    sen, sensor = check_sensor(sid)
    for line in sen:
        print(line)
    print()

    model_id = (sensor or {}).get("model_id") or find_model(sid)
    print("Fensterherleitung ohne Sensor (Ebene S4):")
    print("   Modell-ID aus Transcript: %s" % (model_id or "— nicht gefunden"))
    for line in check_rules(model_id):
        print(line)
    print()

    print("Aktive Kaskadenebene:")
    print("   %s" % active_level(sid, sensor, model_id))
    print()

    if not has_statusline:
        print("→ Ohne registrierte Status-Line kann die Fenstergröße nicht gemessen")
        print("  werden. Sie ist die einzige Stelle, an der Claude Code")
        print("  context_window_size herausgibt. install.sh richtet sie ein.")
    elif sensor and not sensor.get("window"):
        print("→ Die Status-Line läuft, liefert aber kein context_window_size.")
        print("  Claude Code aktualisieren, oder window_override in der config.json")
        print("  setzen (Ebene S3).")
    elif not sensor:
        resolved = model_id and "resolved" in active_level(sid, sensor, model_id)
        if resolved:
            print("→ Kein Sensor, aber das Fenster ist über Ebene S4 belegt — die Anzeige")
            print("  ist korrekt. Der Sensor wäre trotzdem genauer: er liefert Claude Codes")
            print("  eigene Tokenzählung und Kosten statt der aus dem Transcript")
            print("  rekonstruierten. Läuft die Status-Line nicht an, hilft meist ein")
            print("  Neustart des Clients — die Registrierung wird beim Start gelesen.")
        else:
            print("→ Status-Line ist registriert, hat aber nie geschrieben, und auch die")
            print("  Herleitung greift nicht. Prüfen, ob der Client die Status-Line")
            print("  ausführt: nach einer Assistant-Antwort müsste in")
            print("  %s eine Datei liegen." % STATE_DIR)

    if sensor:
        print()
        print("Zuletzt gemessen:")
        for k in ("model_name", "model_id", "window", "tokens_in", "used_pct",
                  "cost_usd", "effort", "version"):
            if sensor.get(k) is not None:
                print("   %-12s %s" % (k, sensor[k]))


if __name__ == "__main__":
    main()
