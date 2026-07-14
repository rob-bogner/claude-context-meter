#!/usr/bin/env python3
"""Translation strings for claude-context-meter.

Every user-visible word lives here. Add a language by copying the "en" block,
translating the values, and setting `"language": "<code>"` in config.json.

Placeholders in the `instruction` string ({block}) are filled at runtime — keep
them intact when translating.
"""

STRINGS = {
    "en": {
        "context": "Context",
        "session": "Session",
        "week": "Week",
        "sonnet_week": "Sonnet",
        "unpushed": "unpushed",
        "reset_now": "now",
        # Recommendation shown on line 3, keyed by tier (green/yellow/orange/red).
        "hint_green": "All clear",
        "hint_yellow": "Keep an eye on it",
        "hint_orange": "Prepare a handoff",
        "hint_red": "Start a handoff / new session now",
        # The instruction handed back to Claude. {block} is the rendered dashboard.
        "instruction": (
            "CONTEXT METER (automatic Stop hook). As your very next reply, output "
            "EXACTLY the following block (multiple lines) VERBATIM and nothing else — "
            "no extra text, no tool calls, no follow-up question:\n{block}"
        ),
    },
    "de": {
        "context": "Kontext",
        "session": "Session",
        "week": "Woche",
        "sonnet_week": "Sonnet",
        "unpushed": "ungepusht",
        "reset_now": "jetzt",
        "hint_green": "Alles im grünen Bereich",
        "hint_yellow": "Im Blick behalten",
        "hint_orange": "Handoff vorbereiten",
        "hint_red": "Jetzt Handoff / neue Session starten",
        "instruction": (
            "KONTEXT-METER (automatischer Stop-Hook). Gib dem Nutzer als deine nächste "
            "Antwort GENAU den folgenden Block (mehrere Zeilen) WORTWÖRTLICH aus und "
            "sonst NICHTS — kein weiterer Text, keine Tool-Aufrufe, keine Rückfrage:\n{block}"
        ),
    },
}

_FALLBACK = "en"


def translator(language):
    """Return a t(key) function for `language`, falling back to English per-key."""
    lang = (language or _FALLBACK).lower()
    table = STRINGS.get(lang, STRINGS[_FALLBACK])
    base = STRINGS[_FALLBACK]

    def t(key):
        return table.get(key, base.get(key, key))

    return t
