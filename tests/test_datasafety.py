import sys
import json
import tempfile
from pathlib import Path
from bonsai_under_test import load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-datasafety-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "max_read_chars": 8000})
work = box / "work"
work.mkdir()
ga.save_trusted([str(work)])

R = ga.REDACTED

print("\n-- a saved file survives a write that fails half way --")
# Opening a file for writing truncates it at once, so a crash, a kill, a full disk or a
# value json cannot encode left nothing behind. Everything the app owns is saved through
# save_json: settings, memory, tasks, chats, the project ledger.
target = box / "settings-under-test.json"
ga.save_json(target, {"server_url": "http://localhost:8085", "theme": "Midnight"})
before = target.read_text()
try:
    # json.dump writes incrementally, so by the time this raises the old code had
    # already truncated the file and written part of the new content into it.
    ga.save_json(target, {"keep": ["x"] * 200, "bad": object()})
    check("the bad write raised", False, True)
except TypeError:
    check("the bad write raised", True, True)
check("the file that was already there is untouched", target.read_text(), before)
check("and is still readable", json.loads(target.read_text())["theme"], "Midnight")
check("no half-written temporary is left behind",
      [p.name for p in box.iterdir() if "saving" in p.name], [])

ga.save_json(target, {"theme": "Bonsai Green"})
check("an ordinary save still works", json.loads(target.read_text())["theme"],
      "Bonsai Green")

# The chat list globs for *.json, so a temporary must never look like a chat.
ga.CHATS_DIR.mkdir(parents=True, exist_ok=True)
ga.save_json(ga.CHATS_DIR / "chat-1.json", {"messages": []})
check("nothing in the chats folder but the chat",
      sorted(p.name for p in ga.CHATS_DIR.glob("*.json")), ["chat-1.json"])

print("\n-- credentials are blanked before the model is shown a file --")
CONFIG = "\n".join([
    "# deployment config",
    "DB_PASSWORD=hunter2correcthorse",
    "db_host = localhost",
    "port = 5432",
    'api_key: "sk-abcdefghijklmnopqrstuvwxyz123456"',
    "client_secret = 'a9f3c1d8e77b'",
    "DATABASE_URL=postgres://admin:s3cr3tpw@db.internal:5432/app",
    "aws_key = AKIAIOSFODNN7EXAMPLE",
    "github = ghp_abcdefghijklmnopqrstuvwxyz0123456789",
]) + "\n"
redacted, count = ga.redact_secrets(CONFIG)
check("every secret went", count, 6)
for leaked in ["hunter2correcthorse", "sk-abcdefghijklmnopqrstuvwxyz123456", "a9f3c1d8e77b",
               "s3cr3tpw", "AKIAIOSFODNN7EXAMPLE", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"]:
    check(f"{leaked[:18]!r} is gone", leaked in redacted, False)
check("the key names stay, so the settings are still legible",
      all(k in redacted for k in ["DB_PASSWORD", "api_key", "client_secret", "DATABASE_URL"]),
      True)
check("and what is not a secret is untouched",
      all(k in redacted for k in ["db_host = localhost", "port = 5432"]), True)
check("the host survives inside a connection string", "db.internal:5432/app" in redacted, True)

print("\n-- and ordinary settings are not mangled --")
SAFE = "\n".join([
    "token_count = 512",              # a count, not a token
    "password_hint = mother's maiden name",
    "max_tokens: 8192",
    "password = \"\"",                # nothing there to hide
    "secret = ${VAULT_SECRET}",       # an indirection, not the value
    "api_key = changeme",             # a placeholder
    "auth_url = https://example.com/oauth",
    "tokens_per_second = 38",
])
kept, n = ga.redact_secrets(SAFE)
check("nothing was redacted", n, 0)
check("the text came back exactly as it went in", kept, SAFE)

print("\n-- a private key is blanked but still recognisable --")
PEM = ("-----BEGIN RSA PRIVATE KEY-----\n"
       "MIIEowIBAAKCAQEAxV2Hn8Kd9pQ2LmN4\nvBqR7sT1uW3xY5zA6bC8dE0fG2hJ4kL6\n"
       "-----END RSA PRIVATE KEY-----\n")
blanked, n = ga.redact_secrets(PEM)
check("the body is gone", "MIIEowIBAAKCAQEAxV2Hn8Kd9pQ2LmN4" in blanked, False)
check("but the file still says what it was",
      "-----BEGIN RSA PRIVATE KEY-----" in blanked, True)
check("counted once", n, 1)

print("\n-- reading a file redacts it and says so --")
check("a file whose name gives it away never gets this far",
      ga.read_file(f"{work / 'app.env'}"), 
      "[Refused: path matches a sensitive/credential file pattern.]")

(work / "main.tf").write_text(CONFIG)
out = ga.read_file(f"{work / 'main.tf'}")
check("the password never reaches the reply", "hunter2correcthorse" in out, False)
check("the reply says values were replaced", "secret value(s) replaced" in out, True)
check("it says the file on disk is unchanged", "unchanged" in out, True)
check("and warns against writing it back", "Do NOT write this text back" in out, True)
check("the real file still has its secrets",
      "hunter2correcthorse" in (work / "main.tf").read_text(), True)

(work / "plain.py").write_text("def add(a, b):\n    return a + b\n")
out = ga.read_file(f"{work / 'plain.py'}")
check("a file with no secrets gets no note", "replaced" in out, False)

out = ga.read_file(f"{work / 'main.tf'}", numbered=False)
check("an attached file is redacted too", "hunter2correcthorse" in out, False)

print("\n-- and the placeholder can never be written back over the real thing --")
# This is what makes redaction safe. Without it, reading a config and writing it out
# again would replace every credential with the placeholder and lose them for good.
was = (work / "main.tf").read_text()
out = ga.do_write(str(work / "main.tf"), redacted)
check("the write is refused", out.startswith("[Refused:"), True)
check("it says why", "stands where a secret was blanked" in out, True)
check("it says nothing was written", "NOTHING was written" in out, True)
check("it points at the tool that would be right", "EDIT" in out, True)
check("the file on disk still has every secret", (work / "main.tf").read_text(), was)

out = ga.do_write(str(work / "fine.py"), "def add(a, b):\n    return a + b\n")
check("an ordinary write is unaffected", out.startswith("Wrote "), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
