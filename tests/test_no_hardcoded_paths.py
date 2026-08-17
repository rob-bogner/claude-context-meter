#!/usr/bin/env python3
"""Guard: no machine-specific paths anywhere in the repository.

This is a public repo. A path that only exists on the author's machine — an
absolute home directory, a developer's checkout location — turns into a
"No such file or directory" for everyone else, and leaks a username on top.
It happened once: TROUBLESHOOTING.md told readers to run doctor.py out of
`~/GitPrivate/`, a directory that exists on exactly one computer.

Scripts must derive their paths (`os.path.expanduser`, `__file__`, `$HOME`);
documentation must name only the locations the installer actually creates.

Run directly:   python3 tests/test_no_hardcoded_paths.py
Or with pytest: pytest -q
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SELF = os.path.relpath(os.path.abspath(__file__), REPO)

# Absolute home directories of any user, on any platform. Assembled from parts so
# this file does not trip its own check.
ABSOLUTE_HOME = re.compile(r"/(?:Users|home)/(?!<)[A-Za-z0-9._-]+")

# `~/` is fine, but only for locations the project itself owns: the Claude config
# directory and the XDG clone target. `~/GitPrivate`, `~/Projects`, `~/Desktop`
# and friends are somebody's private layout.
ALLOWED_TILDE = ("~/.claude", "~/.local/share", "~/.config", "~/.cache")
TILDE = re.compile(r"~/[A-Za-z0-9._/-]+")

# Windows drive letters and UNC paths.
WINDOWS_ABS = re.compile(r"\b[A-Za-z]:\\\\?[A-Za-z0-9._\\-]+|\\\\\\\\[A-Za-z0-9._-]+")

SKIP_DIRS = {".git", "__pycache__", ".remember", "node_modules", "state"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip"}


def tracked_files():
    """Every file git knows about — that is exactly what gets published."""
    try:
        out = subprocess.check_output(
            ["git", "-C", REPO, "ls-files", "-z"], stderr=subprocess.DEVNULL
        )
        names = [n for n in out.decode("utf-8").split("\0") if n]
        if names:
            return names
    except (subprocess.CalledProcessError, OSError):
        pass
    # Fallback for an exported tarball with no git metadata.
    names = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            names.append(os.path.relpath(os.path.join(root, f), REPO))
    return names


def offenders():
    """(file, line number, matched text) for every machine-specific path."""
    found = []
    for name in tracked_files():
        if name == SELF or os.path.splitext(name)[1].lower() in BINARY_SUFFIXES:
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
            for pattern in (ABSOLUTE_HOME, WINDOWS_ABS):
                for m in pattern.finditer(line):
                    found.append((name, n, m.group(0)))
            for m in TILDE.finditer(line):
                hit = m.group(0)
                if not any(hit.startswith(a) for a in ALLOWED_TILDE):
                    found.append((name, n, hit))
    return found


def test_no_hardcoded_paths():
    bad = offenders()
    assert not bad, "machine-specific paths found:\n" + "\n".join(
        "  %s:%d  %s" % (f, n, t) for f, n, t in bad
    )


def test_scripts_derive_their_own_location():
    """The modules that need a base directory must compute it, not spell it out."""
    for module in ("context.py", "sensor.py", "models_api.py", "usage.py"):
        path = os.path.join(REPO, "src", module)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        assert "expanduser" in src or "__file__" in src, (
            "%s hardcodes its location instead of deriving it" % module
        )


if __name__ == "__main__":
    bad = offenders()
    if bad:
        print("❌ machine-specific paths found:")
        for f, n, t in bad:
            print("  %s:%d  %s" % (f, n, t))
        sys.exit(1)
    test_scripts_derive_their_own_location()
    print("✅ no machine-specific paths in %d tracked files" % len(tracked_files()))
