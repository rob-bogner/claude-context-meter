# How it works

Two things are worth understanding in depth: the **Stop-hook mechanism** (how a
block gets into the chat) and **window detection** (how the tool knows whether you
are on a 200k or a 1M context — the part that is genuinely tricky).

## 1. The Stop-hook mechanism

Claude Code fires a `Stop` hook when the assistant finishes a turn. The hook
receives a JSON object on stdin:

```json
{
  "session_id": "3d4933cd-…",
  "transcript_path": "/…/<session>.jsonl",
  "cwd": "/path/to/project",
  "stop_hook_active": false
}
```

A hook can print `{"decision": "block", "reason": "<text>"}`. `block` tells Claude
Code **not** to end the turn yet and to continue with `reason` as guidance. Our
`reason` instructs the assistant to output the dashboard verbatim — so the block
appears as the assistant's next message.

### The loop guard

After the assistant emits the block, it stops again → the hook fires again. This
time Claude Code sets `stop_hook_active: true`. The hook returns immediately in
that case:

```python
if ev.get("stop_hook_active"):
    return
```

Without this guard the hook would block forever. **Consequence:** the block is
shown once per "stop continuation". If the assistant keeps working after a block
instead of stopping (e.g. long tool chains), later stops in the same chain carry
`stop_hook_active: true` and stay silent until the next user message resets it.

### The first-turn race

On the very first turn of a session the hook can fire *before* the assistant
message (with its `usage`) is flushed to the transcript, so `last_context_tokens`
briefly reads nothing. The hook retries once after a short delay:

```python
tokens = last_context_tokens(tpath)
if not tokens:
    time.sleep(0.4)
    tokens = last_context_tokens(tpath)
```

This is best-effort — under heavy load a first block may still slip to the next
turn.

## 2. Window detection (200k vs 1M)

This is the crux. The percentage is meaningless without the correct denominator,
and finding it is harder than it looks.

### Why the obvious sources don't work

- **The transcript model id has no size marker.** It stores the base id, e.g.
  `claude-opus-4-8` — *not* `claude-opus-4-8[1m]`. The `[1m]` marker only exists in
  the client UI. So you cannot tell from the id alone whether 1M is active.
- **`settings.json` / `~/.claude.json` don't store the active model.** The chosen
  model is not persisted anywhere a hook can read.
- **Session metadata has `model: null`.** The `~/.claude/sessions/*.json` registry
  knows the session id and cwd, but not the model or window.
- **The status line knows — but may not run.** Claude Code hands the status line
  the exact `context_window.context_window_size` (200000 or 1000000). But the hook
  never receives that field, and some clients (the current VS Code extension)
  don't render a status line at all, so that channel can be empty.

### The resolution order

`read_window()` tries four sources, most-exact first:

1. **Sensor file** — if a status line ran, it wrote the real
   `context_window_size` to `state/<session>.window`. Exact; wins outright.
2. **Model → window map** — read the model from the transcript and map its family
   to a window via `model_windows` in config (`opus-4-8 → 1000000`, `sonnet →
   200000`, …). This is the normal path in the IDE, where no sensor file exists.
3. **Empirical safety net** — the context can never hold more tokens than the
   window is large. If observed tokens exceed 200k, the window must be at least the
   next known tier (1M). This catches unknown/new models.
4. **Fallback** — 200000, the conservative default.

```
sensor file?  ──yes──▶ use it
     │no
model in map? ──yes──▶ use mapped window
     │no
tokens > 200k?──yes──▶ lift to 1M (next tier)
     │no
   200k
```

The empirical net also runs *after* steps 1–2 as a floor: whatever window we
picked, it is raised if the observed tokens wouldn't fit. So the meter can never
show more than 100%.

### Adding a model

When a new model ships, add one line to `model_windows` in `config.json`:

```json
"model_windows": { "opus-5": 1000000, "opus-4-8": 1000000, "sonnet": 200000 }
```

Keys are matched as case-insensitive substrings of the transcript model id, first
match wins — so list more specific keys before generic ones.

## 3. Cost estimate

`session_cost()` sums every assistant `usage` in the transcript and prices input,
cache-write, cache-read, and output tokens separately using `prices_per_mtok`
(default: Claude Opus 4.8). It is an estimate from token counts, not a billing
figure, and assumes one model's pricing for the whole session.
