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

box = Path(tempfile.mkdtemp(prefix="bonsai-vault-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","SCREEN_LOG_FILE",
          "TASKS_FILE","SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "INTERESTS_FILE","BRIEFING_FILE"]:
    setattr(ga, n, box / n.lower())
vault = box / "Vault"; (vault / "Bonsai").mkdir(parents=True)
(vault / ".obsidian").mkdir()
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault)})

(vault / "ADR_001_Core_Architecture.md").write_text(
    "# ADR 001: Core Architecture\n\n## Decision\n"
    "Hub-and-Spoke Logistics with Buffer Scaling. All primary resources flow to a "
    "single Mother Machine.\n")
(vault / "Bonsai" / "Belt handshake.md").write_text(
    "# Belt handshake\n\nTwo-phase: the belt reserves a slot, the machine confirms.\n")
(vault / "Groceries.md").write_text("- oat milk\n- coffee\n")
(vault / ".obsidian" / "workspace.md").write_text("internal obsidian state\n")

print("\n-- it sees the whole vault, not only its own folder --")
notes = [n.name for n in ga.vault_notes()]
check("the user's own note is included", "ADR_001_Core_Architecture.md" in notes, True)
check("its own note too", "Belt handshake.md" in notes, True)
check("obsidian internals excluded", any(".obsidian" in str(n) for n in ga.vault_notes()), False)
check("three notes found", len(notes), 3)

print("\n-- the prompt carries an index of titles --")
index = ga.vault_index()
check("counts them", "3 notes" in index, True)
check("lists the user's note", "ADR_001_Core_Architecture" in index, True)
check("titles are extensionless", "ADR_001_Core_Architecture.md" in index, False)
check("the vault root is stated so paths can be built",
      str(vault) in index, True)
check("tells it to search before researching", "before researching" in index, True)
check("the index reaches the system prompt",
      "ADR_001_Core_Architecture" in ga.build_system_prompt(), True)
check("but not the note bodies", "Hub-and-Spoke" in ga.build_system_prompt(), False)

print("\n-- searching finds content, wherever it lives --")
out = ga.search_vault("mother machine")
check("finds the user's own note", "ADR_001_Core_Architecture" in out, True)
check("  ...as an absolute path READ_FILE can open",
      str(vault / "ADR_001_Core_Architecture.md") in out, True)
check("quotes the relevant passage", "Hub-and-Spoke" in out, True)
check("says how to read the whole thing", "READ_FILE any of these paths" in out, True)
check("finds its own notes too", "Belt handshake" in ga.search_vault("handshake"), True)
check("plurals still match", "Belt handshake" in ga.search_vault("handshakes"), True)
check("a clean miss says so", ga.search_vault("zeppelins").startswith("[Nothing in 3"), True)
check("needs a term", ga.search_vault("a of").startswith("[Give SEARCH_VAULT"), True)

print("\n-- output stays bounded --")
big = "\n".join(f"line {i} about belts and reservations" for i in range(4000))
(vault / "Huge.md").write_text(big)
out = ga.search_vault("belts")
check("capped well under the context window", len(out) < 4000, True)

print("\n-- no vault configured --")
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})
check("index empty", ga.vault_index(), "")
check("search explains why", ga.search_vault("x").startswith("[No vault configured"), True)
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault)})

print("\n-- reachable through the tool plumbing --")
check("native", ga.native_call_to_tool("search_vault", {"query": "belts"}),
      ("SEARCH_VAULT", "belts"))
check("bracketed text form", ga.extract_tool_call("[TOOL: SEARCH_VAULT: belts]"),
      ("SEARCH_VAULT", "belts"))
check("in the schemas",
      "search_vault" in [s["function"]["name"] for s in ga.build_tool_schemas()], True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
