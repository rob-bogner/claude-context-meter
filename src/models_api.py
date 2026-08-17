#!/usr/bin/env python3
"""claude-context-meter — model capacity from Anthropic's Models API.

`GET /v1/models/{id}` returns `max_input_tokens` — a model's maximum window
size, straight from Anthropic. That is the factual answer to the question no
local table can keep answering:

    claude-opus-5      → 1000000
    claude-sonnet-5    → 1000000
    claude-haiku-4-5   →  200000

A future model reports its value the same way — there is nothing to maintain
here and nothing that can go stale.

Auth: the same OAuth token Claude Code itself uses (macOS keychain, service
"Claude Code-credentials"). Verified: the token may read `/v1/models` (HTTP 200).
Without a token or without network access this falls back to the cache silently.

Caching is aggressive (7 days by default): model capacities practically never
change, and a Stop hook must not add network latency to every turn. The first
lookup per model id costs one request, everything after that is free.

IMPORTANT — what this file does NOT do: it only answers "how large CAN the model
be?". Whether the running session actually uses that maximum is the client's
decision; those rules live in `client_rules.py`.
"""
import os, json, time, subprocess, urllib.request, urllib.error

HOME = os.path.expanduser("~")
STATE_DIR = os.environ.get(
    "CONTEXT_METER_STATE", os.path.join(HOME, ".claude", "context-meter", "state")
)
CACHE_FILE = os.path.join(STATE_DIR, "models.json")

API_URL = "https://api.anthropic.com/v1/models/%s"
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_ACCOUNT = "claude-code-user"

TTL_OK = 7 * 24 * 3600      # known capacity: one week
TTL_MISS = 6 * 3600         # unknown id (404/error): retry sooner
HTTP_TIMEOUT = 2.5          # a hook must never hang


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
def _maybe_unhex(s):
    t = s.strip()
    if len(t) >= 4 and len(t) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in t):
        try:
            return bytes.fromhex(t).decode("utf-8").strip()
        except Exception:
            pass
    return t


def _keychain(account=None):
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


def get_token():
    """Return a valid bearer token, or None."""
    tok = os.environ.get("CONTEXT_METER_OAUTH_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    for account in (KEYCHAIN_ACCOUNT, None):
        raw = _keychain(account)
        if not raw:
            continue
        try:
            oauth = json.loads(raw).get("claudeAiOauth", {})
            token, exp = oauth.get("accessToken"), oauth.get("expiresAt")
            if token and not (isinstance(exp, (int, float)) and exp / 1000 <= time.time()):
                return token
        except Exception:
            if raw.startswith("sk-ant"):
                return raw
    return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _read_cache():
    try:
        with open(CACHE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_cache(cache):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp.%d" % os.getpid()
        with open(tmp, "w") as f:
            json.dump(cache, f, separators=(",", ":"))
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def _fetch(model_id, token):
    req = urllib.request.Request(
        API_URL % urllib.request.quote(model_id, safe=""),
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-context-meter",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def max_input_tokens(model_id, allow_network=True):
    """Maximum window size for `model_id`, or None.

    Order: fresh cache → network → stale cache. A network error is never fatal;
    in doubt the function returns None and the caller honestly shows "unknown"
    instead of an invented number.
    """
    if not model_id:
        return None
    # Claude Code appends variant suffixes the API does not know
    # (`claude-opus-5[1m]` → 404). For the capacity question the base id is what
    # counts; the suffix is evaluated separately by `client_rules.py`.
    base = model_id.split("[")[0].strip()

    cache = _read_cache()
    hit = cache.get(base)
    now = time.time()
    if isinstance(hit, dict):
        age = now - (hit.get("ts") or 0)
        ttl = TTL_OK if hit.get("max_input_tokens") else TTL_MISS
        if age < ttl:
            return hit.get("max_input_tokens")

    if not allow_network:
        return hit.get("max_input_tokens") if isinstance(hit, dict) else None

    token = get_token()
    if token:
        try:
            data = _fetch(base, token)
            val = data.get("max_input_tokens")
            cache[base] = {
                "ts": now,
                "max_input_tokens": int(val) if isinstance(val, (int, float)) else None,
                "max_tokens": data.get("max_tokens"),
                "display_name": data.get("display_name"),
            }
            _write_cache(cache)
            return cache[base]["max_input_tokens"]
        except urllib.error.HTTPError as e:
            # 404 = unknown id (e.g. a client-side alias). Cache the miss so not
            # every turn asks again.
            if e.code == 404:
                cache[base] = {"ts": now, "max_input_tokens": None, "error": 404}
                _write_cache(cache)
                return None
        except Exception:
            pass

    return hit.get("max_input_tokens") if isinstance(hit, dict) else None


def display_name(model_id):
    """Display name from the cache (e.g. "Claude Opus 5"), otherwise None."""
    if not model_id:
        return None
    hit = _read_cache().get(model_id.split("[")[0].strip())
    return hit.get("display_name") if isinstance(hit, dict) else None


if __name__ == "__main__":
    import sys
    for mid in (sys.argv[1:] or ["claude-opus-5", "claude-haiku-4-5", "claude-sonnet-5"]):
        print("%-28s max_input_tokens=%s  %s"
              % (mid, max_input_tokens(mid), display_name(mid) or ""))
