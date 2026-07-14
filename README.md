# claude-context-meter

A tiny [Claude Code](https://claude.com/claude-code) **Stop hook** that prints a
compact context dashboard after every assistant reply — so you always know how
full your context window is, what the session costs, and when it is time to hand
off before you hit the wall.

```
🟢 Context 🟩🟩🟩🟨🟨🟨🟧⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛  32% · 318k/1M · 💰 $0.42 · ⇡4 unpushed
📊 Session 🟩🟩⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛  10% (↻3h) · Week 16% (↻5d) · Sonnet 2% (↻5d)
💡 Keep an eye on it
```

- **Line 1 — Context:** used percentage, a 20-segment gradient bar, tokens vs. the
  real window (`1M` or `200k`), estimated session cost, and unpushed commits.
- **Line 2 — Usage** *(optional)*: your Claude subscription limits (5-hour session,
  7-day week, 7-day Sonnet) with reset countdowns.
- **Line 3 — Recommendation:** a one-liner that escalates from *All clear* to
  *Start a handoff now* as the context fills.

The leading emoji and color escalate 🟢 → 🟡 → 🟠 → 🔴 across configurable
thresholds, with an optional macOS sound the moment you cross into a higher band.

## Why

Claude Code shows a context indicator, but it is easy to lose track mid-task — and
if you run a **1M-token** model the built-in percentage can be misleading. This
hook puts an honest, always-current readout right in the chat, and it detects the
**real** window size (200k vs 1M) on its own. See
[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) for the detection logic.

## Requirements

- Claude Code (CLI, or the VS Code / JetBrains extension)
- `python3` (standard library only — no pip installs)
- `jq` (used by the installer to patch `settings.json` safely)
- Line 2 (subscription usage) is **macOS only** and optional; everything else is
  cross-platform.

## Installation

### Step 0 — Check the prerequisites

```bash
python3 --version     # any Python 3
jq --version          # e.g. brew install jq
claude --version      # Claude Code installed and logged in
```

### Step 1 — Clone the repository

```bash
git clone https://github.com/rob-bogner/claude-context-meter.git
cd claude-context-meter
```

### Step 2 — Run the installer

```bash
./install.sh
```

…or, if you use a terminal client that renders a status line and want the exact
window sensor too:

```bash
./install.sh --with-statusline
```

You can also set the hook timeout (seconds, default 10):

```bash
./install.sh --timeout 15
```

The installer:

1. copies `context_meter.py`, `usage.py`, `i18n.py`, and the status-line script to
   `~/.claude/context-meter/`;
2. writes a default `config.json` there **only if none exists** (your edits are
   never overwritten on re-install);
3. backs up `~/.claude/settings.json` to `settings.json.context-meter.bak`, then
   registers the Stop hook **idempotently** — any other hooks you have are left
   untouched, and re-running never creates duplicates;
4. compiles the scripts and validates the JSON to confirm a clean install.

### Step 3 — Verify

Start a new Claude Code session (or just send a message). After the assistant
replies, the context block appears. To confirm the hook is registered:

```bash
jq '.hooks.Stop' ~/.claude/settings.json      # should list context_meter.py
```

To drive the hook manually against your latest transcript:

```bash
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
printf '{"session_id":"t","transcript_path":"%s","cwd":"%s","stop_hook_active":false}' "$T" "$PWD" \
  | python3 ~/.claude/context-meter/context_meter.py
```

You should see a JSON object containing `"decision":"block"`.

### Updating

```bash
cd claude-context-meter && git pull && ./install.sh
```

Re-running the installer refreshes the scripts and keeps your `config.json`.

### Uninstalling

```bash
./uninstall.sh            # removes the hook, keeps files & config
./uninstall.sh --purge    # also deletes ~/.claude/context-meter/
```

## Configuration

All settings live in **`~/.claude/context-meter/config.json`**. It is plain JSON
and is read fresh on every event — no restart needed, just save and send your next
message. Missing keys fall back to defaults, so you only need to list what you
change. Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### Keys at a glance

| Key | Default | Meaning |
|-----|---------|---------|
| `language` | `"en"` | Output language (`en` / `de`; add your own in `i18n.py`) |
| `bands` | `[15, 30, 45]` | Yellow / orange / red thresholds in % |
| `display_min_tokens` | `6000` | Stay silent below this context load (absolute tokens) |
| `segments` | `20` | Number of cells in the bar (20 = 5% resolution) |
| `features.usage` | `true` | Show line 2 — subscription usage (needs an OAuth token; macOS) |
| `features.cost` | `true` | Show 💰 session cost |
| `features.git_ahead` | `true` | Show ⇡ unpushed commits |
| `features.sound` | `true` | Play a sound on band-up (macOS) |
| `model_windows` | see file | Map a model family → context window (200k / 1M) |
| `prices_per_mtok` | Opus 4.8 | USD per million tokens, for the cost estimate |
| `sounds` | Tink / Sosumi | macOS sounds for the orange / red up-transition |

### Common tweaks

**Switch the language to German:**
```json
{ "language": "de" }
```

**Get warned earlier (yellow at 10%, orange at 25%, red at 40%):**
```json
{ "bands": [10, 25, 40] }
```

**Minimal, quiet, line 1 only (no usage, no sound):**
```json
{ "features": { "usage": false, "sound": false, "cost": true, "git_ahead": true } }
```

**Teach it a new model's window** (keys are case-insensitive substrings of the
transcript model id, first match wins — list specific keys first):
```json
{ "model_windows": { "opus-5": 1000000, "opus-4-8": 1000000, "sonnet": 200000 } }
```

**Price the cost estimate for a different model:**
```json
{ "prices_per_mtok": { "input": 3.0, "cache_write": 3.75, "cache_read": 0.30, "output": 15.0 } }
```

For quick experiments without editing the file, environment variables override it:
`CONTEXT_METER_LANG=de`, `CONTEXT_METER_BANDS="10,25,40"`,
`CONTEXT_METER_CONFIG=/path/to/other.json`. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) for every key and override.

## How it works (in one paragraph)

A Stop hook runs when the assistant finishes a turn. This one reads the session
**transcript** to get the tokens currently in context and the model in use, maps
the model to its window size, renders the block, and returns it via
`{"decision":"block", ...}` — which Claude Code emits as the assistant's next
message. A `stop_hook_active` guard prevents that from looping. The deep dive,
including why the window is derived from the transcript and not the status line,
is in [docs/](docs/).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, files on disk
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) — Stop-hook mechanics & window detection
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every config key
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — when the block doesn't show

## License

MIT — see [LICENSE](LICENSE).
