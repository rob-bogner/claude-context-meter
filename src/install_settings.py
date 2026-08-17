#!/usr/bin/env python3
"""claude-context-meter — registriert Status-Line und Hooks in settings.json.

Aufruf:  python3 src/install_settings.py <install_dir> [--uninstall] [--dry-run]

Idempotent und additiv. Der Installer fasst nur an, was er selbst besitzt:

  * `statusLine` — der Sensor. PFLICHT, nicht optional: ohne ihn kann die
    Fenstergröße nicht gemessen werden, und das Meter fällt auf die ehrliche,
    aber magere Unbekannt-Anzeige zurück. Ist bereits eine fremde Status-Line
    eingetragen, wird sie NICHT überschrieben, sondern in einen Wrapper gefasst,
    der sie aufruft und ihre Ausgabe durchreicht.
  * `hooks.Stop` — das Dashboard. Ein vorhandener Eintrag eines Vorgängers
    (context-meter, session-context-alarm) wird ersetzt, damit nicht zwei
    Dashboards gleichzeitig feuern. Fremde Stop-Hooks bleiben unangetastet.
  * `hooks.SessionStart` — die Modellzeile. Wird ergänzt; fremde
    SessionStart-Hooks (z. B. Vault-Loader) bleiben erhalten.

Vor jeder Änderung wird eine Sicherung mit Zeitstempel abgelegt.
"""
import sys, os, json, shutil, time

HOME = os.path.expanduser("~")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")

# Woran der Installer eigene bzw. abgelöste Einträge erkennt.
OWNED_MARKERS = ("context-meter", "context_meter", "session-context-alarm",
                 "session_start.py", "sensor.py")


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        sys.exit("settings.json ist nicht lesbar (%s) — Abbruch, nichts geändert." % e)


def backup(path):
    if not os.path.exists(path):
        return None
    dst = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, dst)
    return dst


def is_ours(cmd):
    return any(m in (cmd or "") for m in OWNED_MARKERS)


def strip_ours(settings, event):
    """Eigene/abgelöste Einträge aus einem Hook-Event entfernen, fremde behalten."""
    groups = settings.get("hooks", {}).get(event) or []
    kept, removed = [], []
    for g in groups:
        hooks = [h for h in (g.get("hooks") or []) if not is_ours(h.get("command"))]
        removed += [h["command"] for h in (g.get("hooks") or []) if is_ours(h.get("command"))]
        if hooks:
            g = dict(g, hooks=hooks)
            kept.append(g)
        elif not g.get("hooks"):
            kept.append(g)
    if kept:
        settings.setdefault("hooks", {})[event] = kept
    else:
        settings.get("hooks", {}).pop(event, None)
    return removed


def add_hook(settings, event, command, timeout=None):
    entry = {"type": "command", "command": command}
    if timeout:
        entry["timeout"] = timeout
    groups = settings.setdefault("hooks", {}).setdefault(event, [])
    for g in groups:
        if g.get("matcher", "") == "":
            g.setdefault("hooks", []).append(entry)
            return
    groups.append({"matcher": "", "hooks": [entry]})


def install(install_dir, dry_run=False):
    src = os.path.join(install_dir, "src")
    sensor = os.path.join(src, "sensor.py")
    meter = os.path.join(src, "context_meter.py")
    start = os.path.join(src, "session_start.py")
    for p in (sensor, meter, start):
        if not os.path.exists(p):
            sys.exit("Fehlt: %s — erst install.sh ausführen." % p)

    settings = load(SETTINGS)
    log = []

    # --- Status-Line (Sensor) ---------------------------------------------
    sl = settings.get("statusLine")
    cmd = 'python3 "%s"' % sensor
    if isinstance(sl, dict) and sl.get("command") and not is_ours(sl["command"]):
        wrapper = os.path.join(install_dir, "statusline", "wrapper.sh")
        if not dry_run:
            os.makedirs(os.path.dirname(wrapper), exist_ok=True)
            with open(wrapper, "w") as f:
                f.write(
                    "#!/bin/sh\n"
                    "# Von claude-context-meter erzeugt. Führt den Sensor UND die zuvor\n"
                    "# eingetragene Status-Line aus und gibt deren Ausgabe aus.\n"
                    "input=$(cat)\n"
                    'printf "%%s" "$input" | python3 "%s" >/dev/null 2>&1\n'
                    'printf "%%s" "$input" | %s\n' % (sensor, sl["command"])
                )
            os.chmod(wrapper, 0o755)
        settings["statusLine"] = {"type": "command", "command": wrapper}
        log.append("Status-Line: vorhandene Zeile in Wrapper gefasst → %s" % wrapper)
    else:
        settings["statusLine"] = {"type": "command", "command": cmd, "padding": 0}
        log.append("Status-Line: Sensor registriert (%s)" % ("neu" if not sl else "aktualisiert"))

    # --- Stop-Hook (Dashboard) --------------------------------------------
    for old in strip_ours(settings, "Stop"):
        log.append("Stop: abgelösten Eintrag entfernt → %s" % old)
    add_hook(settings, "Stop", 'python3 "%s"' % meter, timeout=10)
    log.append("Stop: Dashboard registriert")

    # --- SessionStart (Modellzeile) ---------------------------------------
    for old in strip_ours(settings, "SessionStart"):
        log.append("SessionStart: abgelösten Eintrag entfernt → %s" % old)
    add_hook(settings, "SessionStart", 'python3 "%s"' % start, timeout=5)
    log.append("SessionStart: Modellzeile registriert (fremde Hooks bleiben)")

    return settings, log


def uninstall():
    settings = load(SETTINGS)
    log = []
    sl = settings.get("statusLine")
    if isinstance(sl, dict) and is_ours(sl.get("command")):
        settings.pop("statusLine", None)
        log.append("Status-Line entfernt")
    for event in ("Stop", "SessionStart"):
        for old in strip_ours(settings, event):
            log.append("%s entfernt → %s" % (event, old))
    return settings, log


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    args = [a for a in args if not a.startswith("--")]
    if "--uninstall" in sys.argv:
        settings, log = uninstall()
    else:
        if not args:
            sys.exit("Aufruf: install_settings.py <install_dir> [--uninstall] [--dry-run]")
        settings, log = install(os.path.abspath(args[0]), dry_run=dry)

    if dry:
        print("--- dry-run, nichts geschrieben ---")
        for line in log:
            print("  " + line)
        print(json.dumps({k: settings.get(k) for k in ("statusLine", "hooks")},
                         indent=2, ensure_ascii=False))
        return

    b = backup(SETTINGS)
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, SETTINGS)

    for line in log:
        print("  " + line)
    if b:
        print("  Sicherung: %s" % b)


if __name__ == "__main__":
    main()
