# Contributing

## The one rule that shapes this codebase

**The app enforces, the model judges.** A local model will claim it wrote a file it
never wrote, mark work done that was never done, and quote tool output it invented.
Every attempt to fix that with better prompt wording has been gamed. What works is
mechanism: check the claim against the filesystem, cap the loop, gate completion on
evidence.

So when you add a capability, ask what the model will get wrong about it, and make the
app check that thing. Prefer a mechanism that verifies over an instruction that asks.

## Running the tests

```
QT_QPA_PLATFORM=offscreen python3 tests/run_all.py
```

No model server needed - the suite exercises pure functions and Qt widgets offline.
There are also live harnesses (`tests/scenarios*.py`) that need a running server; those
catch what unit tests cannot, and have repeatedly found bugs the suite could not see.

## Writing tests

Assert *meaning*, not source text. Counting occurrences of a string, or checking that
two lines sit next to each other, produces false failures the moment someone inserts a
line - this has happened here more than once. Walk the AST or check behaviour instead.

Tests must sandbox their config before anything runs:

```python
for name in ["SETTINGS_FILE", "TRUSTED_PATHS_FILE", ...]:
    setattr(ga, name, box / name.lower())
```

Without it a test reads your real settings and can walk your real project folders.

## Layout

| module | what lives there |
|---|---|
| `config.py` | paths, defaults, what may run or be touched |
| `store.py` | settings, memory, skills, notes, the vault |
| `text.py` | parsing replies, and noticing untrue ones |
| `files.py` | reading, finding and changing files |
| `codeintel.py` | does it parse, and what would this change break |
| `shell.py` | commands, launching programs, the web |
| `media.py` | screen capture, images, charts, documents |
| `turns.py` | tasks, evidence, handoffs, learned procedures |
| `chats.py` | conversations and branches |
| `prompt.py` | the system prompt and tool selection |
| `worker.py` | the turn loop and background workers |
| `theme.py`, `widgets.py`, `app.py` | the interface |

Modules import in that order. If you need something from a later module, the
definition is probably in the wrong place.
