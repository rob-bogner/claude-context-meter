# claude-context-meter

A tiny [Claude Code](https://claude.com/claude-code) **Stop hook** that prints a
compact context dashboard after every assistant reply — so you always know how
full your context window is, what the session costs, and when it is time to hand
off before you hit the wall.

```
🧠 Claude Opus 5 · 1M window · effort xhigh
🟢 Context 🟩🟩🟩🟨🟨🟨🟧⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛  32% · 318k/1M · 💰 $0.42 · ⇡4 unpushed
📊 Session 🟩🟩⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛  10% (↻3h) · Week 16% (↻5d) · Sonnet 2% (↻5d)
💡 Keep an eye on it
```

- **Line 1 — Model:** which model is actually running, its real context window,
  and the effort level. Also shown at session start.
- **Line 2 — Context:** used percentage, a 20-segment gradient bar, tokens vs. the
  real window, estimated session cost, and unpushed commits.
- **Line 3 — Usage** *(optional)*: your Claude subscription limits (5-hour session,
  7-day week) with reset countdowns.
- **Line 4 — Recommendation:** a one-liner that escalates from *All clear* to
  *Start a handoff now* as the context fills.

The leading emoji and color escalate 🟢 → 🟡 → 🟠 → 🔴 across configurable
thresholds, with an optional macOS sound the moment you cross into a higher band.

## Why

Claude Code shows a context indicator, but it is easy to lose track mid-task. The
hard part is the denominator: **no hook event carries the context window size**,
and the model id in the transcript never carries the `[1m]` marker — so
`claude-opus-5` names the 200k and the 1M variant alike. Guessing the window from
the model name is wrong in both directions and silently rots with every new model.

This hook doesn't guess. It resolves the window from facts, in a strict cascade —
and where no fact is available it shows the raw token count instead of inventing a
percentage. See [How the window is resolved](#how-the-window-is-resolved).

## Requirements

- Claude Code (CLI, or the VS Code / JetBrains extension)
- `python3` (standard library only — no pip installs)
- `git` (used by the installer and for the ⇡ unpushed indicator)

## Platform support

Developed and tested on **macOS**. The core is plain Python + `git` with no
OS-specific code, so it should run anywhere Claude Code does — but only macOS is
verified. Two features are macOS-specific and degrade quietly elsewhere (they
never crash the hook):

| Part | macOS | Linux | Windows |
|------|:-----:|:-----:|:-------:|
| Context block — line 1 (context %, window, cost, unpushed commits) | ✅ | ✅ expected | ✅ expected |
| Subscription usage — line 2 | ✅ (Keychain) | env token only¹ | env token only¹ |
| Sound on band-up | ✅ (`afplay`) | — | — |
| Installer (`install.sh` / `bootstrap.sh`, bash) | ✅ | ✅ | via WSL / Git Bash |

¹ Line 2 reads the OAuth token from the macOS Keychain. On other platforms, set
`CONTEXT_METER_OAUTH_TOKEN` to enable it, or leave `features.usage = false`.

## Installation

### Quick install (one line)

Requires `python3` and `git` (macOS or Linux; bash or zsh):

```bash
curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash
```

That clones the repo to `~/.local/share/claude-context-meter`, runs the installer,
and registers three things: the **status line** (the sensor), the **Stop hook**
(the dashboard) and the **SessionStart hook** (the model line). An existing status
line is wrapped rather than replaced, and unrelated hooks are left alone.
**Running the exact same command again updates** to the latest version and keeps
your config.

Upgrading from an earlier, differently-named version? Its Stop hook is replaced
automatically — no flag needed, and you won't get two blocks. To start from a
clean slate instead, run the same line with `--uninstall` first (see
[Uninstalling](#uninstalling)).

> ⚠️ Piping a script into `bash` runs remote code. To read it first:
> `curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh -o bootstrap.sh`,
> inspect it, then `bash bootstrap.sh`.

### Manual install

Prefer to clone and inspect everything yourself? Do it in four steps.

#### Step 0 — Check the prerequisites

```bash
python3 --version     # any Python 3 — standard library only
claude --version      # Claude Code installed and logged in
```

#### Step 1 — Clone the repository

```bash
git clone https://github.com/rob-bogner/claude-context-meter.git
cd claude-context-meter
```

#### Step 2 — Run the installer

```bash
./install.sh
```

To point the hooks at this checkout instead of copying the files — `git pull` then
updates the install:

```bash
./install.sh --in-place
```

Other flags: `--dry-run` shows the `settings.json` changes without writing them,
`--no-statusline` skips the sensor (the meter then resolves the window from the
Models API, cascade level S4).

The installer finishes by running `src/doctor.py`, which reports which cascade
level is active and why.

The installer:

1. copies `context_meter.py`, `usage.py`, `i18n.py`, and the status-line script to
   `~/.claude/context-meter/`;
2. writes a default `config.json` there **only if none exists** (your edits are
   never overwritten on re-install);
3. backs up `~/.claude/settings.json` to `settings.json.context-meter.bak`, then
   registers the Stop hook **idempotently** — any other hooks you have are left
   untouched, and re-running never creates duplicates;
4. compiles the scripts and validates the JSON to confirm a clean install.

#### Step 3 — Verify

Start a new Claude Code session (or just send a message). After the assistant
replies, the context block appears. To confirm the hook is registered:

```bash
python3 src/doctor.py     # what's registered, which cascade level is active
```

To drive the hook manually against your latest transcript:

```bash
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
printf '{"session_id":"t","transcript_path":"%s","cwd":"%s","stop_hook_active":false}' "$T" "$PWD" \
  | python3 src/context_meter.py
```

You should see a JSON object containing `"decision":"block"`.

### Updating

If you used the one-line install, just run it again:

```bash
curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash
```

If you cloned manually:

```bash
cd claude-context-meter && git pull && ./install.sh
```

Either way, the scripts are refreshed and your `config.json` is kept.

### Uninstalling

Same one-liner as the install, with `--uninstall`:

```bash
curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash -s -- --uninstall
```

Add `--purge` to delete `~/.claude/context-meter/` (scripts, config, state) as
well, or `--dry-run` to see what would change in `settings.json` without writing
it. If you cloned manually, call the script directly:

```bash
./uninstall.sh            # deregisters everything, keeps files & config
./uninstall.sh --purge    # also deletes ~/.claude/context-meter/
./uninstall.sh --dry-run  # show the settings.json changes, write nothing
```

Either way it removes all three registrations — status line, Stop hook and
SessionStart hook — and leaves every unrelated hook alone. A repository clone is
never deleted; the uninstaller prints its path so you can remove it yourself.

You do **not** need this before an update — re-running the install is enough (see
[Updating](#updating)). Uninstall first only when you want a clean slate: it
resets `settings.json` to the state before the install, and with `--purge` drops
your `config.json` too.

## Configuration

All settings live in **`~/.claude/context-meter/config.json`**. It is plain JSON
and is read fresh on every event — no restart needed, just save and send your next
message. Missing keys fall back to defaults, so you only need to list what you
change. Full reference: [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### Keys at a glance

| Key | Default | Meaning |
|-----|---------|---------|
| `language` | `"en"` | Output language (`en` / `de`; add your own in `i18n.py`) |
| `output_mode` | `"auto"` | `"auto"` picks per client (IDE→bubble, terminal→systemMessage); force with `"block"` / `"system"` |
| `clients` | `["ide","terminal"]` | Where the block shows at all. `["ide"]` = IDE only, `["terminal"]` = terminal only |
| `bands` | `[15, 30, 45]` | Yellow / orange / red thresholds in % of the **real** window |
| `display_min_tokens` | `6000` | Stay silent below this context load (absolute tokens) |
| `segments` | `20` | Number of cells in the bar (20 = 5% resolution) |
| `features.usage` | `true` | Show line 2 — subscription usage (needs an OAuth token; macOS) |
| `features.cost` | `true` | Show 💰 session cost |
| `features.git_ahead` | `true` | Show ⇡ unpushed commits |
| `features.sound` | `true` | Play a sound on band-up (macOS) |
| `features.model_line` | `true` | Show the 🧠 model line |
| `sensor_fresh_secs` | `90` | How long a status-line reading counts as fresh |
| `use_models_api` | `true` | Resolve model capacity via Anthropic's Models API (cached 7 days) |
| `window_override` | `0` | Declare the window yourself when nothing can be measured |
| `prices_per_mtok` | per family | USD per million tokens — cost fallback when the sensor has none |
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

**Teach it a new model's window:** nothing to do. Window sizes are never
configured — they are measured or resolved from Anthropic's Models API. If you
work fully offline, declare it once instead:
```json
{ "window_override": 1000000 }
```

**Price the cost estimate for a different model family:**
```json
{ "prices_per_mtok": { "default": { "input": 3.0, "output": 15.0 } } }
```

For quick experiments without editing the file, environment variables override it:
`CONTEXT_METER_LANG=de`, `CONTEXT_METER_BANDS="10,25,40"`,
`CONTEXT_METER_CONFIG=/path/to/other.json`. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) for every key and override.

## How the window is resolved

Five levels, strict priority, no backwards overriding — a derivation never
corrects a measurement:

| | Source | Confidence | Shows |
|---|---|---|---|
| **S1** | Status line sensor, fresh | measured | percentage |
| **S2** | Status line sensor, stale (window is fixed for a session) | measured | percentage |
| **S3** | `window_override` from config or `CONTEXT_METER_WINDOW` | declared | percentage + `*` |
| **S4** | Models API capacity + Claude Code's own client rules | resolved | percentage |
| **S5** | nothing verifiable | unknown | **token count only, no alarm** |

**S1/S2 — the sensor.** The status line is the one place where Claude Code hands
out `context_window.context_window_size`. `sensor.py` records it (plus tokens,
model, cost and rate limits) as JSON; the hook only renders. Install it and the
meter stops computing anything it can measure.

**S4 — facts, not a table.** Without a sensor, two facts combine:
`GET /v1/models/{id}` returns `max_input_tokens` for the transcript's model id,
and `client_rules.py` reproduces Claude Code's own window rule (de-minified from
the shipped binary) to decide whether the client uses that capacity:

```
[1m] in the model id                    → 1_000_000
1M beta header + model supports it      → 1_000_000
model in registry && first party        → 1_000_000
otherwise                               →   200_000
```

Every input is checkable locally: env vars are inherited by the hook, the model id
is in the transcript, the capacity comes from the API. **No model name appears
anywhere in this project** — a future model resolves correctly with no update.

**S5 — the honest floor.** If none of the above holds, the meter prints
`⚪ Context 201k loaded · window unknown (≥1M)` — no percentage, no color band, no
sound, no handoff advice. A percentage without a known window is a claim, and that
claim is exactly what produced a bogus *"100% · 201k/200k"* alarm in an earlier
version.

Run the diagnosis to see which level is active and why:
`python3 ~/.claude/context-meter/src/doctor.py` after the one-line install, or
`python3 src/doctor.py` from a clone.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, files on disk
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) — Stop-hook mechanics & window detection
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every config key
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — when the block doesn't show
- `doctor.py` — live diagnosis of the cascade
  (`python3 ~/.claude/context-meter/src/doctor.py`)

## License

MIT — see [LICENSE](LICENSE).
