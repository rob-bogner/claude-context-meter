#!/usr/bin/env python3
"""Guard: code and messages are English.

This is a public repo. The project was written in German and the docs were
translated first, which left the source and — worse — every message the
installer and `doctor` print in a language most users do not read.

The one deliberate exception is i18n.py: its non-English values ARE the
translations, which is the whole point of the `language` setting. Everything
around them, including its own comments, still has to be English.

The check is deliberately crude — a list of high-frequency German words plus the
umlauts. It cannot prove a text is English, but it reliably catches a German
sentence left behind in a docstring or a print().

Run directly:   python3 tests/test_english_only.py
Or with pytest: pytest -q
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SELF = os.path.relpath(os.path.abspath(__file__), REPO)

# i18n.py holds translation data by design; only its comments are checked.
TRANSLATION_FILE = os.path.join("src", "i18n.py")

UMLAUTS = re.compile(r"[äöüÄÖÜß]")

# Function words that are common in German and rare-to-absent in English prose.
# "die"/"war"/"in"/"an"/"so"/"bin"/"list" and friends are excluded on purpose —
# they are ordinary English words and would fire constantly.
GERMAN_WORDS = re.compile(
    r"\b(?:"
    r"nicht|nichts|kein|keine|keinen|keiner|"
    r"eine|einen|einem|eines|"
    r"und|oder|aber|sondern|weil|damit|"
    r"wird|werden|wurde|worden|"
    r"kann|kannst|koennen|muss|mussen|soll|sollen|darf|duerfen|"
    r"beim|vom|zum|zur|"
    r"dass|wenn|dann|noch|schon|immer|nur|auch|"
    r"ohne|durch|gegen|zwischen|"
    r"diese|dieser|dieses|jeder|jede|jedes|"
    r"Datei|Dateien|Fenster|Modell|Zeile|Zeilen|Groesse|"
    r"Sensordaten|Statuszeile|Einstellungen|Sicherung|Verzeichnis"
    r")\b",
    re.IGNORECASE,
)

SKIP_DIRS = {".git", "__pycache__", ".remember", "node_modules", "state"}
CHECK_SUFFIXES = {".py", ".sh", ".md", ".json", ".yml", ".yaml", ".txt"}


def tracked_files():
    try:
        out = subprocess.check_output(
            ["git", "-C", REPO, "ls-files", "-z"], stderr=subprocess.DEVNULL
        )
        names = [n for n in out.decode("utf-8").split("\0") if n]
        if names:
            return names
    except (subprocess.CalledProcessError, OSError):
        pass
    names = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            names.append(os.path.relpath(os.path.join(root, f), REPO))
    return names


def _translation_line(name, line):
    """In i18n.py, a line carrying translation data is exempt; a comment is not."""
    if name != TRANSLATION_FILE:
        return False
    stripped = line.strip()
    if stripped.startswith("#"):
        return False
    return '"' in stripped or "'" in stripped


def offenders():
    """(file, line number, matched text) for every line that looks German."""
    found = []
    for name in tracked_files():
        if name == SELF or os.path.splitext(name)[1].lower() not in CHECK_SUFFIXES:
            continue
        path = os.path.join(REPO, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(lines, 1):
            if _translation_line(name, line):
                continue
            m = UMLAUTS.search(line) or GERMAN_WORDS.search(line)
            if m:
                found.append((name, n, line.strip()[:90]))
    return found


def test_everything_is_english():
    bad = offenders()
    assert not bad, "German text found (this repo is English-only):\n" + "\n".join(
        "  %s:%d  %s" % (f, n, t) for f, n, t in bad
    )


def test_translation_file_still_has_its_languages():
    """The guard must not tempt anyone into deleting the translations it skips."""
    sys.path.insert(0, os.path.join(REPO, "src"))
    from i18n import STRINGS
    assert "en" in STRINGS and "de" in STRINGS, "i18n lost a language"
    assert STRINGS["de"]["context"] != STRINGS["en"]["context"], (
        "the German table is no longer translated"
    )


if __name__ == "__main__":
    bad = offenders()
    if bad:
        print("❌ German text found (this repo is English-only):")
        for f, n, t in bad:
            print("  %s:%d  %s" % (f, n, t))
        sys.exit(1)
    test_translation_file_still_has_its_languages()
    print("✅ no German text outside the translation table")
