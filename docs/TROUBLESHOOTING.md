# Troubleshooting

The hook is designed to fail silently, so "nothing happens" is the usual symptom.
Work down this list.

## The block never appears

**1. Is the hook registered?**
```bash
jq '.hooks.Stop' ~/.claude/settings.json
```
You should see an entry whose command contains `context_meter.py`. If not, re-run
`./install.sh`.

**2. Does the hook run at all?** Drive it manually against a real transcript:
```bash
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
printf '{"session_id":"t","transcript_path":"%s","cwd":"%s","stop_hook_active":false}' "$T" "$PWD" \
  | python3 ~/.claude/context-meter/context_meter.py
```
Expected: a JSON object with `"decision":"block"`. If you get a Python traceback,
that's the bug — file it with the trace.

**3. Below the display threshold?** A brand-new session with very little context
can be under `display_min_tokens` (default 6000). That is intentional. Lower it in
`config.json` if you want the block even on tiny sessions.

**4. First turn of a session?** There is a known race where the first turn's usage
isn't in the transcript yet when the hook fires. The hook retries once (0.4 s) but
under load the first block may slip to the next turn. See
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#the-first-turn-race).

**5. Already inside a stop-continuation?** If the assistant kept working after a
prior block (long tool chains), later stops carry `stop_hook_active: true` and the
hook stays quiet by design until your next message. See
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#the-loop-guard).

## Two blocks appear (an old and a new one)

You installed over an **earlier version** that was registered under a different
name (e.g. `session-context-alarm.py`). Both Stop hooks now fire, so you see two
blocks — often in different languages or formats.

The installer detects this and warns. To remove the earlier hook automatically:

```bash
./install.sh --replace-legacy
# one-liner:
curl -fsSL https://raw.githubusercontent.com/rob-bogner/claude-context-meter/main/bootstrap.sh | bash -s -- --replace-legacy
```

To see what's registered and remove an old entry by hand:

```bash
jq -r '.hooks.Stop[]?.hooks[]?.command' ~/.claude/settings.json
# keep only context_meter.py, drop the rest (safe only if you have no other intentional Stop hooks):
tmp=$(mktemp); jq '.hooks.Stop = ([.hooks.Stop[] | .hooks = ((.hooks//[]) | map(select(.command|contains("context_meter.py")))) ] | map(select((.hooks|length)>0)))' ~/.claude/settings.json > "$tmp" && mv "$tmp" ~/.claude/settings.json
```

`--replace-legacy` only removes hooks whose command looks like a context meter
(`context`/`ctx` + `alarm`/`meter`/`monitor`/…); unrelated Stop hooks are kept.

## The window is wrong (shows 200k on a 1M model, or vice versa)

The hook maps the **transcript model id** to a window via `model_windows`. If your
model isn't listed, it falls back to 200k (until observed tokens exceed 200k).

- Check which model your transcript records:
  ```bash
  T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
  jq -r 'select(.message.role=="assistant") | .message.model' "$T" | sort -u
  ```
- Add or correct the entry in `model_windows` in `config.json`. Keys are
  case-insensitive substrings; put specific keys before generic ones.

If you run a terminal client that renders the status line, install it with
`--with-statusline` — the sensor then feeds the hook the exact window and no
mapping is needed.

## Line 2 (subscription usage) is missing

Line 2 is optional and macOS-only. It needs an OAuth token with the `user:profile`
scope, taken from the Claude Code login in the Keychain.

- Self-test:
  ```bash
  python3 ~/.claude/context-meter/usage.py
  ```
  Prints `token: found` or `MISSING`, then the raw windows or `null`.
- `MISSING` → you're logged into Claude Code via a subscription (not just an API
  key), on macOS, and the Keychain entry `Claude Code-credentials` exists. Or set
  `CONTEXT_METER_OAUTH_TOKEN` explicitly.
- Rate-limited (HTTP 429) → the tool backs off up to an hour and keeps showing the
  last known values for up to 6 hours. This is normal; nothing to do.
- Don't need it? Set `features.usage = false`.

## No sound

Sounds are macOS-only (`afplay`) and fire **only on the up-transition** into
orange/red — not every turn. Green→yellow is intentionally silent. Check the
`sounds` paths exist and `features.sound = true`.

## settings.json got messed up

Every install/uninstall backs it up first:
```bash
ls -t ~/.claude/settings.json.context-meter.bak
cp ~/.claude/settings.json.context-meter.bak ~/.claude/settings.json
```

## Reset everything

```bash
./uninstall.sh --purge          # removes hook, status line, and installed files
```
