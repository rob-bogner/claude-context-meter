#!/usr/bin/env python3
"""claude-context-meter — Nachbildung von Claude Codes eigener Fensterregel.

Die Models API (`models_api.py`) beantwortet „wie groß KANN dieses Modell?".
Diese Datei beantwortet „nutzt der Client dieses Maximum auch?" — nach genau den
Regeln, die Claude Code selbst anwendet.

Quelle: das ausgelieferte Binary, v2.1.233 (Build f8d5756, 2026-08-14). Die
relevante Funktionskette entminifiziert:

    $k(model, betas):
        if (env-Override vorhanden)                  return env-Override
        if (1M-Credits blockiert && sonst > 200k)    return 200_000
        return Jju(model, betas)

    Jju(model, betas):
        if KT(model)                                 return 1_000_000
        if betas enthält 1M-Header && f8(model)      return 1_000_000
        if Z2(model)                                 return 1_000_000
        if _Mo(model) != null                        return _Mo(model)   # Sonnet-4.6-Experiment
        return 200_000                                                   # dCr = 200000

    KT(model)   = !CLAUDE_CODE_DISABLE_1M_CONTEXT && /\\[1m\\]/i.test(model)
    Z2(model)   = !CLAUDE_CODE_DISABLE_1M_CONTEXT && <Modell in Registry>
                  && (provider == "firstParty" && Mf() || … || provider == "mantle")
    Mf()        = _CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
                  || !ANTHROPIC_BASE_URL || host(ANTHROPIC_BASE_URL) == "api.anthropic.com"
    Yju()       = CLAUDE_CODE_DISABLE_COMPACT ? CLAUDE_CODE_MAX_CONTEXT_TOKENS : undefined

Entscheidend ist die dritte Regel: bei einem First-Party-Konto bekommt jedes
Modell, das 1M beherrscht, auch 1M — das `[1m]`-Suffix ist dafür NICHT nötig.
Genau deshalb läuft eine Session mit der Transcript-ID `claude-opus-5` real auf
1M, obwohl der Name das nicht verrät.

Alle Eingaben dieser Regeln sind lokal prüfbar: Umgebungsvariablen erbt der Hook
vom Elternprozess, die Modell-ID steht im Transcript, und ob ein Modell 1M
beherrscht, sagt die Models API. Nichts davon ist geraten.

Grenze der Nachbildung: Ist ein Modell in Claude Codes interner Registry nicht
enthalten, greift dort `dCr` (200k), während die Models API womöglich mehr
meldet. Der Fall ist unwahrscheinlich (die Registry wird mit jedem Release
mitgeliefert) und wird durch die empirische Untergrenze aufgefangen — beobachtete
Tokens über dem angenommenen Fenster heben es an.
"""
import os

try:
    from urllib.parse import urlparse
except ImportError:                                  # pragma: no cover
    from urlparse import urlparse

DEFAULT_WINDOW = 200_000        # dCr im Binary
LONG_CONTEXT = 1_000_000        # die Stufe, die die 1M-Regeln vergeben

TRUTHY = ("1", "true", "yes", "on")


def _flag(name):
    return (os.environ.get(name) or "").strip().lower() in TRUTHY


def _int_env(name):
    try:
        v = int(os.environ.get(name) or 0)
        return v if v > 0 else None
    except ValueError:
        return None


def one_m_disabled():
    """uce() — globaler Aus-Schalter für 1M-Kontext."""
    return _flag("CLAUDE_CODE_DISABLE_1M_CONTEXT")


def is_first_party():
    """Mf() — spricht der Client direkt mit api.anthropic.com?"""
    if _flag("_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL"):
        return True
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if not base:
        return True
    try:
        return urlparse(base).hostname == "api.anthropic.com"
    except Exception:
        return False


def third_party_backend():
    """Bedrock/Vertex/Foundry statt First Party? Nur informativ für `doctor`."""
    for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        if _flag(name):
            return name
    return None


def has_1m_suffix(model_id):
    """KT() — trägt die Modell-ID die 1M-Variantenkennung?"""
    return "[1m]" in (model_id or "").lower()


def effective_window(model_id, model_max_tokens):
    """Effektives Fenster nach Claude Codes Regeln.

    `model_max_tokens` ist `max_input_tokens` aus der Models API (oder None).
    Rückgabe: (fenster, regel) — `regel` benennt die greifende Bedingung und
    landet in `doctor`, damit jede Zahl nachvollziehbar bleibt.
    Rückgabe (None, grund), wenn keine Regel greifen kann.
    """
    # 1 — explizites Limit aus der Umgebung (nur zusammen mit DISABLE_COMPACT)
    if _flag("CLAUDE_CODE_DISABLE_COMPACT"):
        env_max = _int_env("CLAUDE_CODE_MAX_CONTEXT_TOKENS")
        if env_max:
            return env_max, "env:CLAUDE_CODE_MAX_CONTEXT_TOKENS"

    # 2 — 1M global abgeschaltet
    if one_m_disabled():
        return DEFAULT_WINDOW, "env:CLAUDE_CODE_DISABLE_1M_CONTEXT"

    # 3 — Variantenkennung im Modellnamen
    if has_1m_suffix(model_id):
        return LONG_CONTEXT, "model-suffix:[1m]"

    # Ohne Kapazitätsangabe lässt sich nichts Belastbares sagen.
    if not model_max_tokens:
        return None, "no-model-capacity"

    # 4 — First Party: das Modell bekommt seine volle Kapazität
    if model_max_tokens > DEFAULT_WINDOW:
        if is_first_party():
            return int(model_max_tokens), "first-party:model-capacity"
        backend = third_party_backend()
        return DEFAULT_WINDOW, "third-party:%s" % (backend or "custom-base-url")

    # 5 — Modell kann ohnehin nicht mehr als der Standard (z. B. Haiku 4.5)
    return int(model_max_tokens), "model-capacity"


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from models_api import max_input_tokens
    print("first_party=%s  1m_disabled=%s  third_party=%s"
          % (is_first_party(), one_m_disabled(), third_party_backend()))
    for mid in (sys.argv[1:] or ["claude-opus-5", "claude-opus-5[1m]",
                                 "claude-haiku-4-5-20251001", "claude-sonnet-5"]):
        cap = max_input_tokens(mid)
        win, rule = effective_window(mid, cap)
        print("  %-28s cap=%-9s → %-9s (%s)" % (mid, cap, win, rule))
