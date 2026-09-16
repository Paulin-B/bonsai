# Bonsai

A local desktop AI assistant that watches your screen, works on your files, and gets
more useful the longer you use it. It talks to any OpenAI-compatible endpoint —
llama.cpp, LM Studio, Ollama — so the model stays on your machine if you want it to.

It is a single PyQt6 app. No account, no telemetry, no cloud.

![Bonsai](docs/screenshot.png)

## What it does

- **Sees your screen.** Attach the current window or monitor to any message, or let it
  watch periodically and speak up on its own.
- **Acts on your machine.** Reads, writes and edits files, runs commands in a
  bubblewrap sandbox, opens programs, downloads things, searches the web through your
  own SearXNG.
- **Makes things.** Charts, PDFs, and images — drawn with Qt, so there is nothing to
  install and nothing to fail.
- **Checks its own work.** It can *look* at what it made — a chart, a page of a PDF, a
  game window it just launched — and see whether it came out right.
- **Knows what it would break.** Every write and edit is parsed. Rename a function or
  change its parameters and it tells you which files still call the old one, before you
  find out the hard way.
- **Remembers.** Facts about you, notes in your Obsidian vault, earlier conversations,
  and procedures it writes for itself after doing the same job three times.

## Why it is built the way it is

A local model will tell you it wrote a file it never wrote. It will mark a task done
against nothing. It will paste text shaped exactly like tool output and invent the
contents of three files that do not exist. Every attempt to fix this by asking more
firmly in the system prompt got gamed.

So Bonsai does not ask. It checks:

- Completing a task requires naming a file that exists, is more than a stub, **and
  parses**.
- Claims about actions are compared against what actually ran, and contradictions are
  flagged in the transcript.
- Repeated identical calls are refused; runaway loops are cut off.
- Text imitating tool output is stripped from replies, and you are told it was invented.

If you contribute, that is the grain to work with. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Requirements

- Python 3.10+
- An OpenAI-compatible model server. A **vision** model is recommended — screen
  watching is the premise.

It runs on Linux, Windows and macOS. What differs between them:

| | Linux (X11) | Linux (Wayland) | Windows | macOS |
|---|---|---|---|---|
| Screen capture | Qt | needs `grim` | Qt | Qt |
| Command sandbox | `bubblewrap` | `bubblewrap` | **none** | **none** |
| Opening files and apps | `xdg-open` | `xdg-open` | `start` | `open` |

Wayland forbids a client from reading the screen without the compositor's consent,
which is why `grim` is needed there and nowhere else. Where no sandbox exists, Bonsai
does not pretend: every command result says `(NOT sandboxed)` so you know what you are
approving.

## Install

```bash
git clone https://github.com/YOUR-NAME/bonsai.git
cd bonsai
pip install -e ".[all]"
bonsai
```

On Windows, the same commands work in PowerShell once Python is installed. Screen
capture and launching programs need nothing extra there; the command sandbox is not
available (see the table above).

Or without installing:

```bash
pip install -r requirements.txt
python -m bonsai
```

## Point it at a model

Open **⚙ Settings → Model** and set **Server URL**. Some common ones:

| Server | URL |
|---|---|
| llama.cpp | `http://localhost:8080/v1/chat/completions` |
| LM Studio | `http://localhost:1234/v1/chat/completions` |
| Ollama | `http://localhost:11434/v1/chat/completions` |

The **model picker in the header** lists whatever the server offers and switches
between them without restarting anything — it takes effect on your next message, and
the conversation carries on. llama.cpp serves one model and ignores the choice; LM
Studio and Ollama will load the one you pick. Press ↻ after starting a server.

There is a `docker-compose.example.yml` if you want llama.cpp and SearXNG set up for
you. Copy it to `docker-compose.yml` and edit the model path.

## Themes

Six palettes and six font stacks, under **⚙ Settings → Appearance**, applied as soon as
you save. Light and dark are themes like any other — there is no separate switch to
disagree with your choice.

![Themes](docs/themes.png)

*Midnight · Paper · Bonsai Green · Cherry Blossom · Sumi Ink · Sea Glass*

Every palette shares the same keys, so adding one is a dict in
[`bonsai/theme.py`](bonsai/theme.py) and nothing else. There is a test that holds each
one to a contrast floor — body text at 7:1, muted text and anything on the accent at
3:1 — so a new theme cannot ship unreadable.

## First things to do

1. **Add a trusted folder** in the sidebar. File operations there skip the permission
   prompt; everywhere else asks first.
2. **Set a vault path** in Settings if you use Obsidian, and notes become markdown files
   Obsidian reads directly. Leave it blank and notes stay in JSON.
3. **Talk to it.** Memory, skills and project notes fill themselves in as you go.

## Where your data lives

Everything is plain JSON under `~/.local/share/`, safe to read, edit or delete:

```
bonsai_settings.json       app settings, including theme and font
bonsai_character.json      who Bonsai is - edit core_traits to change its personality
bonsai_memory.json         what it believes about you
bonsai_skills.json         saved procedures, including ones it wrote itself
bonsai_patterns.json       shapes of work it has noticed repeating
bonsai_projects.json       per-project file ledger and notes
bonsai_chats/              one file per conversation
```

Set `BONSAI_DATA_DIR` to keep separate profiles.

The Notes, Memory, Tasks and Skills tabs let you edit all of it in the app. If it
believes something wrong about you, fix the line.

## Safety

- `sudo`, `su`, `rm`, `dd`, `systemctl`, `curl` and friends are refused outright, as
  arguments to other programs too — `kitty -e rm -rf ~` does not get through.
- Commands run under bubblewrap where available: only the working folder is writable,
  credential directories are masked with tmpfs, and there is no network.
- Paths matching credentials (`.ssh`, `.gnupg`, `.aws/credentials`, tokens, browser
  cookies) are refused for reading and writing.
- Opening a desktop program runs **outside** the sandbox and always asks first.

None of this makes it safe to point at a hostile model. It makes it safe enough to
point at your own project folder.

## Tests

```bash
QT_QPA_PLATFORM=offscreen python3 tests/run_all.py
```

1,349 checks, no model server required. They run against a temporary data directory, so a test run cannot touch your own settings, memory or chats.

## Licence

MIT.
