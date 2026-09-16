import sys
import tempfile
from pathlib import Path
from bonsai_under_test import load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-note-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","SCREEN_LOG_FILE",
          "TASKS_FILE","SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE"]:
    setattr(ga, n, box / n.lower())
vault = box / "Vault"; vault.mkdir()
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault)})

print("\n-- writing a note --")
out = ga.save_vault_note("Belt handshake | Two-phase: belt reserves, machine confirms.")
note = vault / "Bonsai" / "Belt handshake.md"
check("created", note.is_file(), True)
check("reports where", str(note) in out, True)
body = note.read_text()
check("has a title heading", body.startswith("# Belt handshake"), True)
check("records who wrote it", "Written by Bonsai" in body, True)
check("keeps the content", "machine confirms" in body, True)

print("\n-- a second write appends rather than replacing --")
ga.save_vault_note("Belt handshake | Timeout is 500ms.")
body = note.read_text()
check("original kept", "machine confirms" in body, True)
check("addition kept", "Timeout is 500ms" in body, True)
check("separated and dated", body.count("*Added ") , 1)
check("saying the same thing twice is refused",
      ga.save_vault_note("Belt handshake | Timeout is 500ms.").startswith("[Already in"), True)

print("\n-- it stays inside its own subfolder --")
out = ga.save_vault_note("../../escape | nope")
check("path traversal stripped from the title",
      (vault / "Bonsai").resolve() in Path(ga.vault_note_path("../../escape")).parents, True)
check("odd characters stripped", ga.vault_note_path("a/b:c*d?").name, "abcd.md")
check("an empty title is refused", ga.save_vault_note(" | body").startswith("[SAVE_NOTE needs a title"), True)
check("an empty body is refused", ga.save_vault_note("Title |").startswith("[SAVE_NOTE needs something"), True)

print("\n-- no vault configured says so plainly --")
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})
check("refuses clearly", ga.save_vault_note("T | b").startswith("[No vault configured"), True)
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault)})

print("\n-- reachable through the tool plumbing --")
check("native call", ga.native_call_to_tool("save_note", {"title": "T", "content": "b"}),
      ("SAVE_NOTE", "T | b"))
check("bracketed text form", ga.extract_tool_call("[TOOL: SAVE_NOTE: T | body here]"),
      ("SAVE_NOTE", "T | body here"))
check("in the schemas",
      "save_note" in [s["function"]["name"] for s in ga.build_tool_schemas()], True)
check("ordinary prose starting with Note: is not a tool call",
      ga.extract_tool_call("Note: the kernel update matters most."), (None, None))

print("\n-- the skill is discovered --")
check("save-to-obsidian present", "save-to-obsidian" in ga.all_skills(), True)
check("it names the tool", "save_note" in ga.use_skill("save-to-obsidian"), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
