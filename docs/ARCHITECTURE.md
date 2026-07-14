# Architecture

claude-context-meter is a single **Stop hook** plus an optional **status line**.
No daemon, no server, no state beyond a few tiny files. Everything runs
synchronously in the ~10 seconds Claude Code allows a Stop hook.

## Components

```
claude-context-meter/
├── src/
│   ├── context_meter.py   ← the Stop hook (entry point)
│   ├── usage.py           ← optional line 2: subscription usage via OAuth
│   └── i18n.py            ← translation strings (en/de, extensible)
├── statusline/
│   └── context-meter-statusline.sh   ← optional sensor + one-line display
├── config.example.json    ← copied to config.json on install
├── install.sh / uninstall.sh
└── tests/test_context_meter.py
```

After `install.sh`, the runtime layout under the user's home is:

```
~/.claude/
├── settings.json          ← hook (and optionally statusLine) registered here
└── context-meter/
    ├── context_meter.py    usage.py    i18n.py
    ├── context-meter-statusline.sh
    ├── config.json         ← user config (never overwritten on re-install)
    └── state/
        ├── <session>.band       ← last color band, for sound de-dupe
        ├── <session>.window     ← optional sensor value from the status line
        └── usage-cache.json     ← 5-min cache for the subscription endpoint
```

## Data flow (one Stop event)

```
Claude Code ──(JSON on stdin)──▶ context_meter.py
   { session_id, transcript_path, cwd, stop_hook_active }
                                     │
        ┌────────────────────────────┼─────────────────────────────┐
        ▼                            ▼                             ▼
  read transcript              read config.json               read state/
  • last_context_tokens()      • language, bands,             • <sid>.window (sensor)
  • last_model()                 features, prices             • <sid>.band (prev band)
        │                            │                             │
        └────────────┬───────────────┴──────────────┬──────────────┘
                     ▼                                ▼
             read_window(...)                 usage.get_usage()  (optional, cached)
             (sensor ▸ model ▸ net ▸ 200k)          │
                     │                                ▼
                     └──────────▶ build_block() ◀─────┘
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

- **Config over code.** Thresholds, language, prices, model→window mapping, and
  feature toggles live in `config.json`. Environment variables
  (`CONTEXT_METER_CONFIG`, `CONTEXT_METER_LANG`, `CONTEXT_METER_BANDS`) override
  it for tests and one-offs.

- **The status line is a sensor, not a dependency.** When a client renders the
  status line, it feeds the hook the exact window size. When it does not (e.g. the
  current VS Code extension), the hook still works from the transcript model. See
  [HOW-IT-WORKS.md](HOW-IT-WORKS.md).

## What talks to the network

Only `usage.py`, and only for line 2. It calls one Anthropic endpoint
(`/api/oauth/usage`) at most every 5 minutes, cached to disk. Disable it with
`features.usage = false` and the tool never opens a socket or touches the
Keychain.
