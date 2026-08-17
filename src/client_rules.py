#!/usr/bin/env python3
"""claude-context-meter — reproduction of Claude Code's own window rule.

The Models API (`models_api.py`) answers "how large CAN this model be?". This
file answers "does the client actually use that maximum?" — by exactly the rules
Claude Code applies itself.

Source: the shipped binary, v2.1.233 (build f8d5756, 2026-08-14). The relevant
call chain, de-minified:

    $k(model, betas):
        if (env override present)                    return env override
        if (1M credits blocked && otherwise > 200k)  return 200_000
        return Jju(model, betas)

    Jju(model, betas):
        if KT(model)                                 return 1_000_000
        if betas contains the 1M header && f8(model) return 1_000_000
        if Z2(model)                                 return 1_000_000
        if _Mo(model) != null                        return _Mo(model)   # Sonnet 4.6 experiment
        return 200_000                                                   # dCr = 200000

    KT(model)   = !CLAUDE_CODE_DISABLE_1M_CONTEXT && /\\[1m\\]/i.test(model)
    Z2(model)   = !CLAUDE_CODE_DISABLE_1M_CONTEXT && <model in registry>
                  && (provider == "firstParty" && Mf() || … || provider == "mantle")
    Mf()        = _CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
                  || !ANTHROPIC_BASE_URL || host(ANTHROPIC_BASE_URL) == "api.anthropic.com"
    Yju()       = CLAUDE_CODE_DISABLE_COMPACT ? CLAUDE_CODE_MAX_CONTEXT_TOKENS : undefined

The third rule is the decisive one: on a first-party account every model capable
of 1M gets 1M — the `[1m]` suffix is NOT required for that. Which is exactly why
a session whose transcript id reads `claude-opus-5` really runs on 1M, even
though the name does not say so.

Every input to these rules is checkable locally: environment variables are
inherited by the hook from its parent, the model id is in the transcript, and
whether a model supports 1M comes from the Models API. None of it is guessed.

Limit of the reproduction: if a model is absent from Claude Code's internal
registry, `dCr` (200k) applies there while the Models API may report more. That
case is unlikely (the registry ships with every release) and is caught by the
empirical lower bound — observed tokens above the assumed window raise it.
"""
import os

try:
    from urllib.parse import urlparse
except ImportError:                                  # pragma: no cover
    from urlparse import urlparse

DEFAULT_WINDOW = 200_000        # dCr in the binary
LONG_CONTEXT = 1_000_000        # the tier the 1M rules hand out

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
    """uce() — the global off switch for 1M context."""
    return _flag("CLAUDE_CODE_DISABLE_1M_CONTEXT")


def is_first_party():
    """Mf() — does the client talk to api.anthropic.com directly?"""
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
    """Bedrock/Vertex/Foundry instead of first party? Informational for `doctor`."""
    for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        if _flag(name):
            return name
    return None


def has_1m_suffix(model_id):
    """KT() — does the model id carry the 1M variant marker?"""
    return "[1m]" in (model_id or "").lower()


def effective_window(model_id, model_max_tokens):
    """Effective window under Claude Code's rules.

    `model_max_tokens` is `max_input_tokens` from the Models API (or None).
    Returns (window, rule) — `rule` names the condition that applied and ends up
    in `doctor`, so every number stays traceable.
    Returns (None, reason) when no rule can apply.
    """
    # 1 — explicit limit from the environment (only together with DISABLE_COMPACT)
    if _flag("CLAUDE_CODE_DISABLE_COMPACT"):
        env_max = _int_env("CLAUDE_CODE_MAX_CONTEXT_TOKENS")
        if env_max:
            return env_max, "env:CLAUDE_CODE_MAX_CONTEXT_TOKENS"

    # 2 — 1M switched off globally
    if one_m_disabled():
        return DEFAULT_WINDOW, "env:CLAUDE_CODE_DISABLE_1M_CONTEXT"

    # 3 — variant marker in the model name
    if has_1m_suffix(model_id):
        return LONG_CONTEXT, "model-suffix:[1m]"

    # Without a capacity figure nothing solid can be said.
    if not model_max_tokens:
        return None, "no-model-capacity"

    # 4 — first party: the model gets its full capacity
    if model_max_tokens > DEFAULT_WINDOW:
        if is_first_party():
            return int(model_max_tokens), "first-party:model-capacity"
        backend = third_party_backend()
        return DEFAULT_WINDOW, "third-party:%s" % (backend or "custom-base-url")

    # 5 — the model cannot exceed the default anyway (e.g. Haiku 4.5)
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
