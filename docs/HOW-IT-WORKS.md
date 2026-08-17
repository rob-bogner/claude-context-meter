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
  the exact `context_window.context_window_size`. But no hook event receives that
  field, and some clients (the current VS Code extension) don't render a status
  line, so that channel can be empty.

**A model-name lookup table cannot fix this.** It is wrong in both directions —
the same family exists at 200k and at 1M — and it rots silently with every new
model, because a miss produces no error, just a wrong number. An earlier version
shipped exactly that table and displayed `100% · 201k/200k` with a red "hand off
now" while 80% of a 1M window was still free.

### The cascade

`context.resolve()` walks five levels, strict priority. A derivation never
overrides a measurement.

| | Source | Confidence | Renders |
|---|---|---|---|
| **S1** | sensor record for this session, fresh (≤ 90 s) | measured | percentage |
| **S2** | sensor record for this session, stale | measured | percentage |
| **S3** | `window_override` / `CONTEXT_METER_WINDOW` | declared | percentage + `*` |
| **S4** | Models API capacity + client rules | resolved | percentage |
| **S5** | nothing verifiable | unknown | **tokens only** |

S2 is safe because a window size is fixed for a session's lifetime — only the
token count is re-read from the transcript.

### S4 — resolving from facts

Two independent facts combine:

**1. What can the model hold?** `GET /v1/models/{id}` returns `max_input_tokens`
for the model id found in the transcript:

```
claude-opus-5      → 1000000
claude-sonnet-5    → 1000000
claude-haiku-4-5   →  200000
```

Authenticated with the same OAuth token Claude Code uses; cached 7 days. The
`[1m]` variant is *not* an API model (404) — it is a client-side marker, so the
base id is queried.

**2. Does the client use that capacity?** `client_rules.py` reproduces Claude
Code's own rule, de-minified from the shipped binary (v2.1.233):

```
[1m] in the model id                     → 1_000_000
1M beta header + model supports it       → 1_000_000
model in registry && first-party endpoint→ the model's full capacity
otherwise                                →   200_000

first-party = no ANTHROPIC_BASE_URL, or host == api.anthropic.com
disabled by CLAUDE_CODE_DISABLE_1M_CONTEXT
```

The third rule is the one that matters in practice: on a first-party account every
1M-capable model gets 1M — **without** `[1m]` in the name. That is why a session
whose transcript says `claude-opus-5` really runs at 1M.

Every input is checkable locally: hooks inherit the environment, the model id is in
the transcript, the capacity comes from the API.

**Empirical cross-check.** If the observed tokens exceed the resolved window, the
resolution is too small (e.g. a model missing from the client registry). The window
is then lifted to the next known tier and the rule is reported as `…+observed`, so
the contradiction is visible rather than hidden.

### Adding a model

Nothing to do. There is no model list in this project.

## 3. Cost estimate

`session_cost()` sums every assistant `usage` in the transcript and prices input,
cache-write, cache-read, and output tokens separately using `prices_per_mtok`
(default: Claude Opus 4.8). It is an estimate from token counts, not a billing
figure, and assumes one model's pricing for the whole session.
