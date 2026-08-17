#!/usr/bin/env python3
"""claude-context-meter — the sensor cascade.

Answers the one question the whole project hangs on: **how large is this
session's context window, really?** Shared by `context_meter.py` (the Stop hook)
and `session_start.py`.

Why that is not trivial:

  * No hook event carries the window size or the model. The official docs are
    explicit: "Neither model_id nor context window size is passed to hooks",
    and `$CLAUDE_MODEL` does not exist.
  * The model id in the transcript carries NO `[1m]` suffix. Across 14,895
    entries checked, it is always the base id — `claude-opus-5` names the 200k
    and the 1M variant alike. The window therefore cannot be derived from the
    model name in principle, no matter how well a table is maintained.
  * The active model choice is not persisted anywhere a hook could find it —
    neither in settings.json nor in ~/.claude.json.

Hence the cascade. Five levels, strict priority, **no backwards overriding**: a
derivation never corrects a measurement.

  S1  measured        sensor of the running session, fresh          → percentage
  S2  measured_stale  sensor of the same session, older             → percentage
  S3  declared        window_override from config/environment       → percentage
  S4  resolved        Models API + Claude Code's own client rules   → percentage
  S5  unknown         nothing verifiable                            → NO percentage

Level 4 closes the gap that used to end in "window unknown": Anthropic's Models
API returns `max_input_tokens` for the transcript's model id, and the rules
reproduced from the client binary (client_rules.py) decide whether the client
uses that capacity. Both are facts — no name guessing, no locally maintained
table.

Level 5 remains underneath as a backstop. If none of the four apply, the meter
shows the absolute token count instead of a plausible-looking wrong number (the
kind that produced "100% · 201k/200k" and a bogus handoff recommendation back in
June). It cannot raise a false alarm, because it only alarms when the window
size is established.

Model independence: not a single model name appears in this file, nor anywhere
else in the project. A future "Opus 5.2" with a 2M window is detected correctly
with no change here and none in the config — the sensor measures the number, and
without a sensor Anthropic's Models API supplies the model's capacity.
"""
import os, json, time

HOME = os.path.expanduser("~")
BASE_DIR = os.path.join(HOME, ".claude", "context-meter")
STATE_DIR = os.environ.get("CONTEXT_METER_STATE", os.path.join(BASE_DIR, "state"))

# When does a sensor reading still count as "fresh"? The status line runs on
# every new assistant message, but is debounced by 300 ms and can land just
# behind the Stop hook on very fast turns. 90 s is generous enough for long tool
# chains and tight enough to notice a dead status line.
FRESH_SECS = 90

# Known window tiers — used ONLY as the lower bound in the unknown case ("at
# least this large"). NOT for determining the window: 300k tokens being loaded
# does not prove the window is 1M. Adding new tiers here is optional; if one is
# missing, the observed token count becomes the bound itself.
KNOWN_TIERS = (200_000, 1_000_000, 2_000_000)

# Files in the state directory that are caches, not sensor readings. They must
# not be mistaken for "the sensor has written before".
CACHE_FILES = ("models.json", "usage-cache.json")


class Ctx(object):
    """Resolved context state plus the evidence for it.

    `confidence` drives the rendering: anything other than "unknown" may show
    percentages, colour bands and recommendations.
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
# Reading the sensor
# ---------------------------------------------------------------------------
def read_sensor(session_id=None, allow_last=False):
    """Load a sensor file. Without a `session_id` (or with allow_last) this falls
    back to `last.json` — the most recent session that wrote a sensor. That is
    meant for the SessionStart hook, which cannot have its own sensor yet; the
    caller recognises the foreign reading by its differing `session_id`."""
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


def sensor_ever_wrote():
    """Has the sensor ever written a reading? Caches written by other parts of
    the project live in the same directory and must not count — otherwise a
    fresh install looks like a broken one."""
    try:
        return any(n.endswith(".json") and n not in CACHE_FILES
                   for n in os.listdir(STATE_DIR))
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Lower bound (for the unknown case only)
# ---------------------------------------------------------------------------
def floor_for(tokens):
    """Smallest known tier that could hold `tokens`. Purely a statement about a
    lower bound — never use it as the window size."""
    if not tokens:
        return None
    for tier in KNOWN_TIERS:
        if tokens <= tier:
            return tier
    return int(tokens)


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------
def resolve(session_id, cfg=None, transcript_tokens=None, transcript_model=None,
            transcript_effort=None):
    """Resolve the context state.

    `transcript_tokens` / `transcript_model` / `transcript_effort` are the values
    the caller reconstructed from the transcript. They serve as a fallback for
    the display — never to determine the window size.
    """
    cfg = cfg or {}
    sensor = read_sensor(session_id)

    # A sensor belonging to a FOREIGN session is worthless for this one.
    if sensor and session_id and sensor.get("session_id") not in (None, session_id):
        sensor = None

    # --- S1 / S2 — measured -------------------------------------------------
    if sensor and sensor.get("window"):
        age = sensor_age(sensor)
        fresh = age is not None and age <= cfg.get("sensor_fresh_secs", FRESH_SECS)
        # Tokens: a fresh sensor value beats the transcript (it comes from the
        # same source as the window). With an old sensor the WINDOW stays valid —
        # window sizes do not change within a session — while the token count is
        # taken from the transcript.
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
            effort=sensor.get("effort") or transcript_effort,
            cost=sensor.get("cost_usd") if fresh else None,
            rate_limits=sensor.get("rate_limits"),
            sensor=sensor,
        )

    # --- S3 — declared ------------------------------------------------------
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
            effort=transcript_effort,
        )

    # --- S4 — resolved ------------------------------------------------------
    # Two facts, no estimation:
    #   * Anthropic's Models API returns `max_input_tokens` for the model id from
    #     the transcript — the model's capacity.
    #   * Claude Code's own rules (reproduced in client_rules.py from the shipped
    #     binary) decide whether the client makes use of it.
    # Together that gives the effective window, with no local model table and so
    # nothing that could go stale when a new model appears.
    if transcript_model and cfg.get("use_models_api", True):
        try:
            from models_api import max_input_tokens, display_name
            from client_rules import effective_window

            cap = max_input_tokens(transcript_model,
                                   allow_network=cfg.get("allow_network", True))
            win, rule = effective_window(transcript_model, cap)
            if win:
                # Empirical cross-check: more tokens loaded than the derived
                # window could hold means the derivation falls short (e.g. the
                # model is not in the client registry). Lift to the next known
                # tier and mark the contradiction, rather than showing a number
                # that has already been refuted.
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
                    effort=transcript_effort,
                )
        except Exception:
            pass

    # --- S5 — unknown -------------------------------------------------------
    # Deliberately NO model-name-to-window mapping as a fallback. It would be
    # wrong in both directions (the same family exists as 200k and as 1M) and
    # would go stale silently with every new model.
    return Ctx(
        window=None,
        tokens=transcript_tokens,
        floor=floor_for(transcript_tokens),
        source="none",
        confidence="unknown",
        model=transcript_model,
        model_id=transcript_model,
        effort=transcript_effort,
    )


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------
def diagnose(session_id=None):
    """State of the cascade in plain text — the basis for `doctor`."""
    lines = []
    sensor = read_sensor(session_id, allow_last=True)
    if not sensor:
        lines.append("Sensor:      MISSING — no file in %s" % STATE_DIR)
        lines.append("             → the status line is not registered, or never runs.")
        return lines, sensor
    age = sensor_age(sensor)
    own = sensor.get("session_id") == session_id if session_id else None
    lines.append("Sensor:      %s" % os.path.join(STATE_DIR, (session_id or "last") + ".json"))
    lines.append("  Age:       %s" % ("unknown" if age is None else "%.0f s" % age))
    lines.append("  Session:   %s%s" % (sensor.get("session_id"),
                                        "" if own is None else (" (own)" if own else " (FOREIGN)")))
    lines.append("  Window:    %s" % (sensor.get("window") or "— absent from the status-line input"))
    lines.append("  Model:     %s  [%s]" % (sensor.get("model_name") or "?",
                                            sensor.get("model_id") or "?"))
    lines.append("  Tokens:    %s" % (sensor.get("tokens_in") or "—"))
    lines.append("  Cost:      %s" % ("$%.2f" % sensor["cost_usd"]
                                      if isinstance(sensor.get("cost_usd"), (int, float)) else "—"))
    lines.append("  Limits:    %s" % (", ".join(sorted(sensor["rate_limits"]))
                                      if sensor.get("rate_limits") else "—"))
    return lines, sensor
