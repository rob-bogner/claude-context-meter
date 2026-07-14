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

## Install

```bash
git clone https://github.com/<you>/claude-context-meter.git
cd claude-context-meter
./install.sh                 # or: ./install.sh --with-statusline
```

The installer copies the scripts to `~/.claude/context-meter/`, writes a default
`config.json`, and registers the Stop hook in `~/.claude/settings.json`
(idempotently — it backs up your settings and leaves any other hooks alone).

Then start a new session or just send a message: the block appears after the
assistant's reply.

To remove it:

```bash
./uninstall.sh               # keeps files; --purge deletes them too
```

## Configure

Edit `~/.claude/context-meter/config.json`. Full reference:
[docs/CONFIGURATION.md](docs/CONFIGURATION.md). The essentials:

| Key | Default | Meaning |
|-----|---------|---------|
| `language` | `"en"` | Output language (`en` / `de`; add your own in `i18n.py`) |
| `bands` | `[15, 30, 45]` | Yellow / orange / red thresholds in % |
| `display_min_tokens` | `6000` | Stay silent below this context load |
| `features.usage` | `true` | Show line 2 (needs an OAuth token; macOS) |
| `features.cost` | `true` | Show 💰 session cost |
| `features.git_ahead` | `true` | Show ⇡ unpushed commits |
| `features.sound` | `true` | Play a sound on band-up (macOS) |
| `model_windows` | see file | Map a model family to its context window |
| `prices_per_mtok` | Opus 4.8 | USD per million tokens, for the cost estimate |

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
