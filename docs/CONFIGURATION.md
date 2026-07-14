# Configuration

All settings live in `~/.claude/context-meter/config.json` (created from
`config.example.json` on install). It is plain JSON — restart is not needed; the
hook reads it fresh on every event.

Missing keys fall back to built-in defaults, so a partial config is fine.

## Full reference

### `language` — string, default `"en"`
Output language for the block. Ships with `"en"` and `"de"`. Add more by editing
`i18n.py` (copy the `en` block, translate the values). Does not affect the
numbers, only the words.

### `output_mode` — string, default `"auto"`
How the block reaches the chat. The two clients render hooks differently, so no
single mechanism is right for both — `"auto"` detects the client and picks:

- `"auto"` *(recommended)* — reads `CLAUDE_CODE_ENTRYPOINT`: IDE extensions
  (`claude-vscode`, JetBrains) get `block`; everything else (terminal CLI, SSH,
  tmux) gets `system`.
- `"block"` — always `decision:block`; the assistant re-emits the block. A clean
  chat bubble in the IDE, but **doubles in the terminal** (the CLI shows the hook
  feedback *and* the reply).
- `"system"` — always a `systemMessage`; shown once. Great in the terminal, but
  the **IDE extension renders it only partially**.

Why it matters: the IDE extension doesn't show hook feedback (so `block` appears
once) but only partially renders a `systemMessage`. The terminal CLI shows hook
feedback (so `block` appears twice) but renders a `systemMessage` cleanly. `auto`
gives each client the one that looks right. Override only if detection is wrong
for your setup.

### `clients` — array of strings, default `["ide", "terminal"]`
Where the block is shown at all. The hook reads `CLAUDE_CODE_ENTRYPOINT` and
classifies the current client as:

- `"ide"` — the VS Code / JetBrains extensions (`claude-vscode`, …)
- `"terminal"` — everything else (the CLI, SSH, tmux, …)

Only listed clients get the block; in any other client the hook stays silent.

- Both (default): `["ide", "terminal"]`
- **IDE only:** `["ide"]` — nothing in the terminal
- Terminal only: `["terminal"]`

This is independent of [`output_mode`](#output_mode-string-default-auto): `clients`
decides *whether* the block appears, `output_mode` decides *how* it renders.

### `bands` — `[int, int, int]`, default `[15, 30, 45]`
The yellow / orange / red thresholds in **percent of context used**. Below the
first value the meter is green. These drive the leading emoji, the bar colors, the
recommendation text, and the sound. Example for an earlier warning:
`"bands": [10, 25, 40]`.

### `display_min_tokens` — int, default `6000`
The block stays silent while the context holds fewer tokens than this. It is an
**absolute token count**, deliberately not a percentage — at 1M a percentage
threshold would suppress the block until tens of thousands of tokens. 6000 ≈ the
old 3%-of-200k behavior.

### `segments` — int, default `20`
Number of cells in the gradient bar. 20 cells = 5% resolution. Set to 10 for a
shorter bar.

### `features` — object of booleans
| Key | Default | Effect when `true` |
|-----|---------|--------------------|
| `cost` | `true` | Show 💰 estimated session cost on line 1 |
| `usage` | `true` | Show line 2 (subscription usage). Needs an OAuth token; macOS. |
| `git_ahead` | `true` | Show ⇡N unpushed commits when the cwd is a git repo |
| `sound` | `true` | Play a sound the first time you cross into orange/red (macOS) |

Set any to `false` to drop that piece. `usage: false` also means the tool never
touches the network or Keychain.

### `model_windows` — object `{ "<substring>": <tokens> }`
Maps a model to its context window. The key is matched as a **case-insensitive
substring** of the transcript model id; the first match wins, so order matters —
list specific keys before generic ones.

```json
"model_windows": {
  "opus-4-8": 1000000,
  "fable-5":  1000000,
  "sonnet":   200000,
  "haiku":    200000
}
```

When a new model ships, add a line here — no code change needed. See
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#2-window-detection-200k-vs-1m).

### `prices_per_mtok` — object, default = Opus 4.8
USD per **million** tokens, used for the cost estimate:

```json
"prices_per_mtok": {
  "input": 5.00, "cache_write": 6.25, "cache_read": 0.50, "output": 25.00
}
```

If you mostly run a different model, put its prices here. The estimate assumes one
price set for the whole session.

### `sounds` — object `{ "orange": path, "red": path }`
macOS system sounds played on the up-transition into that band. Set a value to
`null` to silence one band. Any `afplay`-playable file works.

```json
"sounds": {
  "orange": "/System/Library/Sounds/Tink.aiff",
  "red":    "/System/Library/Sounds/Sosumi.aiff"
}
```

## Environment overrides

Handy for testing or a one-off run without editing the file:

| Variable | Overrides |
|----------|-----------|
| `CONTEXT_METER_CONFIG` | Path to the config file to load |
| `CONTEXT_METER_LANG` | `language` |
| `CONTEXT_METER_BANDS` | `bands`, e.g. `"10,25,40"` |
| `CONTEXT_METER_OAUTH_TOKEN` | OAuth token for line 2 (bypasses the Keychain) |

## Example: minimal, English, line 1 only

```json
{
  "language": "en",
  "features": { "cost": true, "usage": false, "git_ahead": true, "sound": false }
}
```
