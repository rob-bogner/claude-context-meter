#!/usr/bin/env python3
"""claude-context-meter — der Sensor (Status-Line-Kommando).

Claude Code übergibt dem Status-Line-Kommando bei jeder neuen Assistant-Nachricht
ein JSON auf stdin, das die einzigen autoritativen Laufzeitwerte der Session
enthält — allen voran `context_window.context_window_size`, die REALE Fenster-
größe. Kein Hook-Event enthält diesen Wert, und die Modell-ID im Transcript kann
200k und 1M nicht unterscheiden (sie trägt kein `[1m]`-Suffix). Deshalb ist diese
Datei die Grundlage des gesamten Projekts: sie misst, alles andere rendert nur.

Zwei Aufgaben:

  1. SENSOR — schreibt den vollständigen Zustand als JSON nach
     ~/.claude/context-meter/state/<session_id>.json (atomar via tmp+rename,
     damit ein gleichzeitig lesender Hook nie eine halbe Datei sieht) sowie
     nach state/last.json, damit der SessionStart-Hook schon vor dem ersten
     Turn etwas anzeigen kann.

  2. ANZEIGE — gibt eine kompakte Zeile aus. Terminals rendern sie; die
     VS-Code-Extension zeigt derzeit keine Status-Line, dort zählt allein
     der Seiteneffekt aus (1).

Bewusst ohne `jq`: reines Python3 spart eine Abhängigkeit, die auf frischen
Systemen oft fehlt, und kostet nur ~40 ms pro Aufruf (Status-Line-Updates sind
auf 300 ms entprellt).

Der Sensor schreibt NUR, was Claude Code selbst geliefert hat. Fehlt ein Feld,
bleibt es `null` — es wird nichts geschätzt, geraten oder aus dem Modellnamen
abgeleitet. Genau daran hängt die Modellunabhängigkeit: ein künftiges Modell
mit unbekanntem Namen liefert seine Fenstergröße hier trotzdem korrekt mit.
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
    """Verschachtelten Wert holen; None, sobald ein Glied fehlt oder kein dict ist."""
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _window(cw):
    """Fenstergröße: bevorzugt das explizite Feld, sonst aus Tokens und Prozent
    zurückgerechnet (Claude Code liefert `used_percentage` auch dann, wenn
    `context_window_size` in einer älteren Version noch fehlt)."""
    w = _num(_dig(cw, "context_window_size"))
    if w and w > 0:
        return int(w)
    tok, pct = _num(_dig(cw, "total_input_tokens")), _num(_dig(cw, "used_percentage"))
    if tok and pct and pct > 0:
        return int(round(tok / pct * 100))
    return None


def _limits(rl):
    """rate_limits auf das Nötige eindampfen. `resets_at` ist Unix-Epoch-Sekunden."""
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
        # --- die autoritativen Werte ---
        "window": _window(cw),
        "tokens_in": _num(cw.get("total_input_tokens")),
        "tokens_out": _num(cw.get("total_output_tokens")),
        "used_pct": _num(cw.get("used_percentage")),
        "exceeds_200k": ev.get("exceeds_200k_tokens"),
        # --- Modell: Anzeige-Name und ID, NICHT zur Fensterableitung ---
        "model_id": _dig(ev, "model", "id"),
        "model_name": _dig(ev, "model", "display_name"),
        # --- Kosten rechnet Claude Code selbst; keine eigene Preistabelle nötig ---
        "cost_usd": _num(_dig(ev, "cost", "total_cost_usd")),
        # --- Abo-Verbrauch: ersetzt den OAuth-/Keychain-Weg vollständig ---
        "rate_limits": _limits(_dig(ev, "rate_limits")),
        # --- Kontext fürs Rendern ---
        "effort": _dig(ev, "effort", "level"),
        "fast_mode": ev.get("fast_mode"),
        "thinking": _dig(ev, "thinking", "enabled"),
        "version": ev.get("version"),
        "cwd": _dig(ev, "workspace", "current_dir") or ev.get("cwd"),
        "git_branch": _dig(ev, "workspace", "git_branch"),
    }


def write_state(state):
    """Atomar schreiben: erst tmp, dann rename. os.replace ist auf POSIX atomar,
    ein parallel lesender Stop-Hook sieht also immer eine vollständige Datei."""
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
        pass  # Ein Sensorfehler darf die Status-Line nie zum Absturz bringen.


# ---------------------------------------------------------------------------
# Anzeige (nur dort sichtbar, wo Claude Code eine Status-Line rendert)
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
