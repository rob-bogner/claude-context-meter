# Architecture

claude-context-meter is a **sensor** (status line) plus two **renderers** (a Stop
hook and a SessionStart hook). No daemon, no server, no state beyond a few tiny
files. Everything runs synchronously in the ~10 seconds Claude Code allows a Stop
hook — typically well under half a second.

The split is the whole design: the status line is the only place Claude Code hands
out the real context window size, so it *measures* and writes a small JSON record;
the hooks only *render* it. Where no sensor ran, the window is resolved from
Anthropic's Models API plus Claude Code's own client rules — never from a
model-name lookup table.

## Components

```
claude-context-meter/
├── src/
│   ├── sensor.py           ← the status line: measures and records (SENSOR)
│   ├── context.py          ← the five-level cascade; shared resolver
│   ├── models_api.py       ← model capacity from /v1/models (cached 7 days)
│   ├── client_rules.py     ← Claude Code's own window rule, reproduced
│   ├── context_meter.py    ← the Stop hook: renders the dashboard
│   ├── session_start.py    ← the SessionStart hook: renders the model line
│   ├── doctor.py           ← diagnosis: which cascade level is active, and why
│   ├── install_settings.py ← registers status line + hooks in settings.json
│   ├── usage.py            ← fallback for line 3 when the sensor has no limits
│   └── i18n.py             ← translation strings (en/de, extensible)
├── config.example.json     ← copied to config.json on install
├── install.sh / uninstall.sh
└── tests/test_context_meter.py
```

After `install.sh`, the runtime layout under the user's home is:

```
~/.claude/
├── settings.json          ← statusLine + Stop + SessionStart registered here
└── context-meter/
    ├── config.json        ← user config (never overwritten on re-install)
    └── state/
        ├── <session>.json      ← the sensor record (window, tokens, model,
        │                          cost, rate limits, effort, timestamp)
        ├── last.json           ← copy of the most recent record, for the
        │                          SessionStart hook before its own turn
        ├── <session>.band      ← last color band, for sound de-dupe
        ├── models.json         ← model capacities from the Models API (7-day TTL)
        └── usage-cache.json    ← only used by the usage.py fallback
```

## Data flow (one Stop event)

Every new assistant message triggers the status line first — that is what keeps
the sensor current:

```
Claude Code ──(status-line JSON)──▶ sensor.py ──▶ state/<sid>.json + last.json
   { model, context_window, cost, rate_limits, effort, workspace, … }
```

The Stop hook then renders from it:

```
Claude Code ──(JSON on stdin)──▶ context_meter.py
   { session_id, transcript_path, cwd, stop_hook_active }
                     │
        ┌────────────┴────────────┬──────────────────────┐
        ▼                         ▼                      ▼
  read transcript           read config.json        context.resolve()
  • tokens (fallback)       • language, bands,      S1/S2 sensor record
  • model id                  features, prices      S3   window_override
        │                         │                 S4   models_api + client_rules
        │                         │                 S5   unknown → no percentage
        └────────────┬────────────┴──────────────────────┘
                     ▼
              build_block()   ← model line · context line · usage line · hint
                     │
                     ▼
    print {"decision":"block","reason": <instruction+block>}
                     │
                     ▼
   Claude Code continues the turn → assistant emits the block
```

## Design choices

- **Stateless rendering.** Every field is derived fresh from the transcript on
  each event. The only persisted state is the previous color band (so a sound
  fires once per up-crossing, not every turn) and the optional sensor value.

- **Fail open, never break the turn.** Any error — unreadable transcript, missing
  token, offline, malformed config — degrades gracefully. Line 2 disappears
  before line 1 does; the hook returns nothing rather than raising.

- **No third-party dependencies.** Pure Python standard library, so it runs
  wherever Claude Code runs without a virtualenv.

- **Config over code — but never for facts.** Thresholds, language, prices and
  feature toggles live in `config.json`. Window sizes deliberately do *not*: they
  are measured or resolved, because a configured window silently rots with every
  new model. Environment variables (`CONTEXT_METER_CONFIG`, `CONTEXT_METER_LANG`,
  `CONTEXT_METER_BANDS`, `CONTEXT_METER_WINDOW`) override the file.

- **No model name anywhere.** Grep the source: there is no model list. A future
  model resolves correctly with no update.

- **Silence beats a plausible wrong number.** If the window cannot be established,
  the meter prints the raw token count — no percentage, no color band, no sound,
  no handoff advice. See [HOW-IT-WORKS.md](HOW-IT-WORKS.md).

## What talks to the network

Two callers, both optional and both cached to disk:

- `models_api.py` — `GET /v1/models/{id}` for the model's `max_input_tokens`.
  Once per model id per **7 days**; 2.5 s timeout; any failure falls back to the
  cache and then to "unknown". Disable with `use_models_api: false` or
  `allow_network: false`.
- `usage.py` — `/api/oauth/usage` for the subscription limits, at most every
  5 minutes. Only reached when the sensor did not already provide `rate_limits`,
  i.e. when no status line ran. Disable with `features.usage: false`.

Both read the OAuth token Claude Code itself uses (macOS Keychain, service
`Claude Code-credentials`). With both disabled the tool never opens a socket.
