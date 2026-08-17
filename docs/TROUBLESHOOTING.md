# Troubleshooting

The hook is designed to fail silently, so "nothing happens" is the usual symptom.
Work down this list.

> **Paths in this document.** The commands below use `$CM`, the directory the
> scripts actually live in. Set it once — this reads it back from the
> registration itself, so it is right for every install variant:
>
> ```bash
> CM=$(python3 -c "import json,os,shlex;s=json.load(open(os.path.expanduser('~/.claude/settings.json')));print(os.path.dirname(shlex.split(s['statusLine']['command'])[-1]))")
> echo "$CM"
> ```
>
> A default install reports `~/.claude/context-meter/src`; with `--in-place` it
> reports your checkout. If it prints nothing, no status line is registered — run
> the installer again.

## The block never appears

**1. Is the hook registered?**
```bash
python3 "$CM/doctor.py"
```
You should see an entry whose command contains `context_meter.py`. If not, re-run
the installer.

**2. Does the hook run at all?** Drive it manually against a real transcript:
```bash
T=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
printf '{"session_id":"t","transcript_path":"%s","cwd":"%s","stop_hook_active":false}' "$T" "$PWD" \
  | python3 "$CM/context_meter.py"
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

Re-run the installer — it replaces earlier context-meter Stop hooks automatically:

```bash
./install.sh
```

It matches on the script names this project has shipped under
(`context_meter.py`, `session-context-alarm.py`, …) and leaves unrelated Stop
hooks alone. To see what is registered:

```bash
python3 -c "import json,os;print(json.dumps(json.load(open(os.path.expanduser('~/.claude/settings.json')))['hooks'],indent=2))"
```

`python3 "$CM/install_settings.py" --uninstall` removes this project's entries and
nothing else.

## The window is wrong (shows 200k on a 1M model, or vice versa)

Run the diagnosis first — it names the active cascade level and the reason:

```bash
python3 "$CM/doctor.py" "$CLAUDE_CODE_SESSION_ID"
```

Typical outcomes:

- **`S1 measured`** — the status line is feeding the sensor; the number is Claude
  Code's own. Nothing to do.
- **`S4 resolved`** — no sensor, but the window was resolved from the Models API
  plus the client rules. Correct, though the sensor would also give you Claude
  Code's token count and cost instead of the reconstructed ones. If the status
  line is registered but never wrote, restart the client — the registration is
  read at startup.
- **`S5 unknown`** — the meter prints the token count without a percentage. Check
  in this order: is a status line registered (`statusLine` in `settings.json`)?
  Is an OAuth token in the Keychain (`doctor` reports it)? Is
  `CLAUDE_CODE_DISABLE_1M_CONTEXT` or a custom `ANTHROPIC_BASE_URL` set? As a last
  resort declare it: `{"window_override": 1000000}`.

**The window looks too small.** Check whether `CLAUDE_CODE_DISABLE_1M_CONTEXT` is
set, or whether `ANTHROPIC_BASE_URL` points somewhere other than
`api.anthropic.com` — both cap the window at 200k by Claude Code's own rules, and
the meter follows them. `doctor` prints both.

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
