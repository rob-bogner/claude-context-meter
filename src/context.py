#!/usr/bin/env python3
"""claude-context-meter — die Sensor-Kaskade.

Beantwortet die eine Frage, an der das ganze Projekt hängt: **wie groß ist das
Kontextfenster dieser Session wirklich?** Gemeinsam genutzt von `context_meter.py`
(Stop-Hook) und `session_start.py`.

Warum das nicht trivial ist:

  * Kein Hook-Event enthält die Fenstergröße oder das Modell. Die offizielle
    Doku ist explizit: „Neither model_id nor context window size is passed to
    hooks", und `$CLAUDE_MODEL` existiert nicht.
  * Die Modell-ID im Transcript trägt KEIN `[1m]`-Suffix. Über 14.895 geprüfte
    Einträge steht dort immer nur die Basis-ID — `claude-opus-5` bezeichnet die
    200k- und die 1M-Variante gleichermaßen. Aus dem Modellnamen lässt sich das
    Fenster deshalb prinzipiell nicht ableiten, egal wie gut die Tabelle gepflegt
    ist.
  * Die aktive Modellwahl wird nirgends persistiert, wo ein Hook sie fände —
    weder in settings.json noch in ~/.claude.json.

Daraus folgt die Kaskade. Fünf Ebenen, strikte Priorität, **kein Rückwärts-
Überschreiben**: eine Herleitung korrigiert niemals eine Messung.

  S1  measured        Sensor der laufenden Session, frisch          → Prozent
  S2  measured_stale  Sensor derselben Session, älter               → Prozent
  S3  declared        window_override aus Config/Umgebung           → Prozent
  S4  resolved        Models API + Claude Codes eigene Client-Regeln → Prozent
  S5  unknown         nichts Verlässliches                          → KEIN Prozent

Ebene 4 schließt die Lücke, die früher zu „Fenster unbekannt" führte: Anthropics
Models API liefert `max_input_tokens` zur Modell-ID aus dem Transcript, und die
aus dem Client-Binary nachgebildeten Regeln (client_rules.py) entscheiden, ob der
Client diese Kapazität ausschöpft. Beides sind Fakten — kein Namensraten, keine
lokal gepflegte Tabelle.

Ebene 5 bleibt als Sicherung darunter: Greift ausnahmsweise keine der vier
Ebenen, zeigt das Meter nur die absolute Tokenzahl statt einer plausibel
aussehenden Falschzahl (die im Juni zu „100% · 201k/200k" und einer falschen
Handoff-Empfehlung geführt hat). Es kann nicht falsch alarmieren, weil es nur
alarmiert, wenn die Fenstergröße belegt ist.

Modellunabhängigkeit: In dieser Datei steht kein einziger Modellname, und auch
sonst nirgends im Projekt. Ein künftiges „Opus 5.2" mit 2M Fenster wird korrekt
erkannt, ohne dass hier oder in der Config etwas angepasst werden muss — der
Sensor misst die Zahl, und ohne Sensor liefert Anthropics Models API die
Kapazität des Modells.
"""
import os, json, time

HOME = os.path.expanduser("~")
BASE_DIR = os.path.join(HOME, ".claude", "context-meter")
STATE_DIR = os.environ.get("CONTEXT_METER_STATE", os.path.join(BASE_DIR, "state"))

# Ab wann gilt ein Sensorwert als "frisch"? Die Status-Line läuft bei jeder neuen
# Assistant-Nachricht, ist aber auf 300 ms entprellt und kann bei sehr schnellen
# Turns knapp hinter dem Stop-Hook liegen. 90 s sind großzügig genug für lange
# Tool-Ketten und eng genug, um eine tote Status-Line zu bemerken.
FRESH_SECS = 90

# Bekannte Fenster-Stufen — ausschließlich für die Untergrenze bei S4 ("mindestens
# so groß"). NICHT zur Fensterbestimmung: nur weil 300k geladen sind, ist das
# Fenster nicht bewiesenermaßen 1M. Neue Stufen hier zu ergänzen ist optional;
# fehlt eine, wird die beobachtete Tokenzahl selbst zur Untergrenze.
KNOWN_TIERS = (200_000, 1_000_000, 2_000_000)


class Ctx(object):
    """Aufgelöster Kontextzustand plus Herkunftsnachweis.

    `confidence` steuert das Rendering: alles außer "unknown" darf Prozente,
    Farbbänder und Handlungsempfehlungen zeigen.
    """

    __slots__ = ("window", "tokens", "floor", "source", "confidence",
                 "model", "model_id", "effort", "cost", "rate_limits", "sensor")

    def __init__(self, window=None, tokens=None, floor=None, source="none",
                 confidence="unknown", model=None, model_id=None, effort=None,
                 cost=None, rate_limits=None, sensor=None):
        self.window = window
        self.tokens = tokens
        self.floor = floor
        self.source = source
        self.confidence = confidence
        self.model = model
        self.model_id = model_id
        self.effort = effort
        self.cost = cost
        self.rate_limits = rate_limits
        self.sensor = sensor

    @property
    def known(self):
        return self.confidence != "unknown" and bool(self.window)

    @property
    def pct(self):
        if not self.known or not self.tokens:
            return None
        return round(self.tokens / self.window * 100)


# ---------------------------------------------------------------------------
# Sensor lesen
# ---------------------------------------------------------------------------
def read_sensor(session_id=None, allow_last=False):
    """Sensordatei laden. Ohne `session_id` (oder mit allow_last) fällt die
    Funktion auf `last.json` zurück — die letzte Session, die einen Sensor
    geschrieben hat. Das ist für den SessionStart-Hook gedacht, der noch keinen
    eigenen Sensor haben kann; der Aufrufer erkennt den Fremdstand daran, dass
    `session_id` im Ergebnis abweicht."""
    names = []
    if session_id:
        names.append(session_id + ".json")
    if allow_last or not session_id:
        names.append("last.json")
    for name in names:
        try:
            with open(os.path.join(STATE_DIR, name)) as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("schema"):
                return data
        except Exception:
            continue
    return None


def sensor_age(sensor):
    ts = sensor.get("ts") if sensor else None
    return None if not isinstance(ts, (int, float)) else max(0.0, time.time() - ts)


# ---------------------------------------------------------------------------
# Untergrenze (nur für den Unbekannt-Fall)
# ---------------------------------------------------------------------------
def floor_for(tokens):
    """Kleinste bekannte Stufe, die `tokens` fassen könnte. Reine Aussage über
    eine Untergrenze — nie als Fenstergröße verwenden."""
    if not tokens:
        return None
    for tier in KNOWN_TIERS:
        if tokens <= tier:
            return tier
    return int(tokens)


# ---------------------------------------------------------------------------
# Die Kaskade
# ---------------------------------------------------------------------------
def resolve(session_id, cfg=None, transcript_tokens=None, transcript_model=None):
    """Kontextzustand auflösen.

    `transcript_tokens` / `transcript_model` sind die aus dem Transcript
    rekonstruierten Werte des Aufrufers. Sie dienen als Rückfallebene für die
    Anzeige — niemals zur Bestimmung der Fenstergröße.
    """
    cfg = cfg or {}
    sensor = read_sensor(session_id)

    # Ein Sensor einer FREMDEN Session ist für diese Session wertlos.
    if sensor and session_id and sensor.get("session_id") not in (None, session_id):
        sensor = None

    # --- S1 / S2 — gemessen -------------------------------------------------
    if sensor and sensor.get("window"):
        age = sensor_age(sensor)
        fresh = age is not None and age <= cfg.get("sensor_fresh_secs", FRESH_SECS)
        # Tokens: der frische Sensorwert schlägt das Transcript (er stammt aus
        # derselben Quelle wie das Fenster). Bei altem Sensor bleibt das FENSTER
        # gültig — Fenstergrößen ändern sich innerhalb einer Session nicht —,
        # während die Tokenzahl aus dem Transcript nachgeführt wird.
        tokens = sensor.get("tokens_in") if fresh else None
        if not tokens:
            tokens = transcript_tokens or sensor.get("tokens_in")
        return Ctx(
            window=int(sensor["window"]),
            tokens=tokens,
            source="statusline",
            confidence="measured" if fresh else "measured_stale",
            model=sensor.get("model_name") or sensor.get("model_id") or transcript_model,
            model_id=sensor.get("model_id"),
            effort=sensor.get("effort"),
            cost=sensor.get("cost_usd") if fresh else None,
            rate_limits=sensor.get("rate_limits"),
            sensor=sensor,
        )

    # --- S3 — deklariert ----------------------------------------------------
    override = cfg.get("window_override") or os.environ.get("CONTEXT_METER_WINDOW")
    try:
        override = int(override) if override else None
    except (TypeError, ValueError):
        override = None
    if override and override > 0:
        return Ctx(
            window=override,
            tokens=transcript_tokens,
            source="override",
            confidence="declared",
            model=transcript_model,
        )

    # --- S4 — hergeleitet ---------------------------------------------------
    # Zwei Fakten, keine Schätzung:
    #   * Anthropics Models API liefert `max_input_tokens` für die Modell-ID
    #     aus dem Transcript — die Kapazität des Modells.
    #   * Claude Codes eigene Regeln (nachgebildet in client_rules.py aus dem
    #     ausgelieferten Binary) entscheiden, ob der Client sie ausschöpft.
    # Zusammen ergibt das die effektive Fenstergröße, ohne lokale Modelltabelle
    # und damit ohne etwas, das bei einem neuen Modell veralten könnte.
    if transcript_model and cfg.get("use_models_api", True):
        try:
            from models_api import max_input_tokens, display_name
            from client_rules import effective_window

            cap = max_input_tokens(transcript_model,
                                   allow_network=cfg.get("allow_network", True))
            win, rule = effective_window(transcript_model, cap)
            if win:
                # Empirische Gegenprobe: mehr geladene Tokens als das
                # hergeleitete Fenster fassen könnte, heißt, die Herleitung
                # greift zu kurz (z. B. Modell nicht in der Client-Registry).
                # Dann auf die nächste bekannte Stufe heben und den Widerspruch
                # kenntlich machen, statt eine widerlegte Zahl zu zeigen.
                if transcript_tokens and transcript_tokens > win:
                    win = floor_for(transcript_tokens) or win
                    rule += "+observed"
                return Ctx(
                    window=win,
                    tokens=transcript_tokens,
                    source="models-api:" + rule,
                    confidence="resolved",
                    model=display_name(transcript_model) or transcript_model,
                    model_id=transcript_model,
                )
        except Exception:
            pass

    # --- S5 — unbekannt -----------------------------------------------------
    # Bewusst KEIN Modellname-zu-Fenster-Mapping als Rückfall. Es wäre in beide
    # Richtungen falsch (dieselbe Familie gibt es als 200k und als 1M) und würde
    # bei jedem neuen Modell still veralten.
    return Ctx(
        window=None,
        tokens=transcript_tokens,
        floor=floor_for(transcript_tokens),
        source="none",
        confidence="unknown",
        model=transcript_model,
    )


# ---------------------------------------------------------------------------
# Diagnose
# ---------------------------------------------------------------------------
def diagnose(session_id=None):
    """Zustand der Kaskade als Klartext — Grundlage für `doctor`."""
    lines = []
    sensor = read_sensor(session_id, allow_last=True)
    if not sensor:
        lines.append("Sensor:      FEHLT — keine Datei in %s" % STATE_DIR)
        lines.append("             → Status-Line ist nicht registriert oder läuft nicht.")
        return lines, sensor
    age = sensor_age(sensor)
    own = sensor.get("session_id") == session_id if session_id else None
    lines.append("Sensor:      %s" % os.path.join(STATE_DIR, (session_id or "last") + ".json"))
    lines.append("  Alter:     %s" % ("unbekannt" if age is None else "%.0f s" % age))
    lines.append("  Session:   %s%s" % (sensor.get("session_id"),
                                        "" if own is None else (" (eigene)" if own else " (FREMDE)")))
    lines.append("  Fenster:   %s" % (sensor.get("window") or "— fehlt im Status-Line-Input"))
    lines.append("  Modell:    %s  [%s]" % (sensor.get("model_name") or "?",
                                            sensor.get("model_id") or "?"))
    lines.append("  Tokens:    %s" % (sensor.get("tokens_in") or "—"))
    lines.append("  Kosten:    %s" % ("$%.2f" % sensor["cost_usd"]
                                      if isinstance(sensor.get("cost_usd"), (int, float)) else "—"))
    lines.append("  Limits:    %s" % (", ".join(sorted(sensor["rate_limits"]))
                                      if sensor.get("rate_limits") else "—"))
    return lines, sensor
