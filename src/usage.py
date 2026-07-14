#!/usr/bin/env python3
"""Optional line 2: reads Claude subscription usage (5h session / 7d week /
7d Sonnet) from Anthropic's OAuth usage endpoint — the same source as
claude.ai/settings/usage and the IDE's Account & Usage card.

Endpoint: GET https://api.anthropic.com/api/oauth/usage
  Headers: Authorization: Bearer <oauth-token>, anthropic-beta: oauth-2025-04-20
  Response: five_hour / seven_day / seven_day_sonnet / seven_day_opus, each
            {utilization (0-100), resets_at (ISO)}.

The endpoint needs an OAuth token with the `user:profile` scope. The live login
token of the running Claude Code binary has it and is kept fresh automatically.
On macOS it lives in the Keychain under service "Claude Code-credentials".

Token sources (first VALID one wins):
  1. Env CONTEXT_METER_OAUTH_TOKEN   (explicit override — works on any OS)
  2. Keychain "Claude Code-credentials", account "claude-code-user"
  3. Keychain "Claude Code-credentials" service-only lookup (covers installs
     where the account name differs) — a single targeted call, no enumeration.

Robust by design: any failure (no token, expired, offline, 401/403, timeout,
non-macOS with no env token) returns None, and the caller simply drops line 2.

This is the ONLY part of claude-context-meter that talks to the network or the
Keychain. Set features.usage = false in config.json to disable it entirely.
"""
import os, json, time, subprocess, urllib.request, urllib.error

KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_ACCOUNT = "claude-code-user"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_FILE = os.path.join(os.path.expanduser("~"), ".claude", "context-meter",
                          "state", "usage-cache.json")
CACHE_TTL = 300         # refresh at most every 5 min (Stop fires per reply; the
                        # usage endpoint has a tight hourly limit -> be frugal)
STALE_GRACE = 21600     # on error/cooldown, keep showing the last data up to 6h
DEFAULT_COOLDOWN = 3600 # on 429 without Retry-After: pause 1h
HTTP_TIMEOUT = 3.0      # a hook must never hang (Stop-hook timeout is ~10s)


def _maybe_unhex(s):
    # `security -w` prints the password as hex once the Keychain blob contains a
    # newline / non-printable char. Detect that and decode back to plain text.
    t = s.strip()
    if len(t) >= 4 and len(t) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in t):
        try:
            return bytes.fromhex(t).decode("utf-8").strip()
        except Exception:
            pass
    return t


def _keychain_raw(account=None):
    # account=None -> service-only lookup (no -a): returns the single/first entry
    # for the service without guessing an account name. One targeted call, no dump,
    # no enumeration. macOS only; silently no-ops elsewhere.
    cmd = ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE]
    if account:
        cmd += ["-a", account]
    cmd += ["-w"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return _maybe_unhex(out.stdout)
    except Exception:
        pass
    return None


def _token_from_raw(raw):
    """Extract a VALID (non-expired) accessToken from a Keychain blob, else None."""
    if not raw:
        return None
    try:
        oauth = json.loads(raw).get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        exp = oauth.get("expiresAt")  # ms
        if not token:
            return None
        if isinstance(exp, (int, float)) and exp / 1000 <= time.time():
            return None
        return token
    except Exception:
        # In case the entry is a raw token rather than JSON.
        return raw if raw.startswith("sk-ant") else None


def get_token():
    """Return a valid Bearer token with user:profile scope, or None."""
    tok = os.environ.get("CONTEXT_METER_OAUTH_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    for account in (KEYCHAIN_ACCOUNT, None):   # targeted, then service-only fallback
        t = _token_from_raw(_keychain_raw(account))
        if t:
            return t
    return None


def _read_cache():
    try:
        with open(CACHE_FILE) as f:
            c = json.load(f)
        return c.get("at", 0), c.get("data"), c.get("cooldown_until", 0)
    except Exception:
        return 0, None, 0


def _write_cache(data, at=None, cooldown_until=0):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({"at": at if at is not None else time.time(),
                       "data": data, "cooldown_until": cooldown_until}, f)
    except Exception:
        pass


class _RateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


def _fetch(token):
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "claude-cli/1.0.0 (external, cli)",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            ra = e.headers.get("Retry-After") if e.headers else None
            try:
                ra = int(ra)
            except (TypeError, ValueError):
                ra = DEFAULT_COOLDOWN
            raise _RateLimited(ra)
        raise

    def win(w):
        if not isinstance(w, dict):
            return None
        u, rs = w.get("utilization"), w.get("resets_at")
        if not isinstance(u, (int, float)) or not isinstance(rs, str):
            return None
        return {"pct": round(u), "resets_at": rs}

    return {
        "five_hour": win(raw.get("five_hour")),
        "seven_day": win(raw.get("seven_day")),
        "seven_day_sonnet": win(raw.get("seven_day_sonnet")),
        "seven_day_opus": win(raw.get("seven_day_opus")),
    }


def get_usage():
    """Return dict with five_hour/seven_day/seven_day_sonnet(/_opus) or None.
    5-min cache. On 429, Retry-After is honored (cooldown, no hammering) and the
    last known state is shown. Any error -> old cache (<=6h) or None."""
    now = time.time()
    at, data, cooldown_until = _read_cache()

    if data and (now - at) < CACHE_TTL:
        return data
    if now < cooldown_until:
        return data if (data and now - at < STALE_GRACE) else None

    token = get_token()
    if not token:
        return data if (data and now - at < STALE_GRACE) else None

    try:
        fresh = _fetch(token)
        _write_cache(fresh)
        return fresh
    except _RateLimited as rl:
        _write_cache(data, at=at, cooldown_until=now + rl.retry_after)
        return data if (data and now - at < STALE_GRACE) else None
    except Exception:
        return data if (data and now - at < STALE_GRACE) else None


def fmt_reset(iso, now_word="now"):
    """Time until reset; minutes below 1h, hours below 24h, else days."""
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = (t - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None
    if secs <= 0:
        return now_word
    mins = secs / 60
    if mins < 60:
        return "%dm" % max(1, round(mins))
    hours = mins / 60
    if hours < 24:
        return "%dh" % round(hours)
    return "%dd" % round(hours / 24)


if __name__ == "__main__":
    # Self-test: shows token status + raw windows.
    print("token:", "found" if get_token() else "MISSING")
    print(json.dumps(get_usage(), indent=2))
