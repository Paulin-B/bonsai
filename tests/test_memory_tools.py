"""Every storage tool is actually callable and does what it says.

Written after RECALL turned out to have been dead for several commits: its function
body had been orphaned by an edit and no test ever called it.
"""
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

box = Path(tempfile.mkdtemp(prefix="bonsai-tools-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.CHATS_DIR = box / "chats"
vault = box / "Vault"; vault.mkdir()
project = box / "factory"; project.mkdir()
ga.save_trusted([str(project)])
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault)})

print("\n-- every tool the worker can dispatch to actually exists --")
for name in ["search_web", "fetch_url", "read_file", "list_directory", "find_files",
             "search_memory", "search_chats", "download_file", "handle_task",
             "save_skill", "use_skill", "do_write", "do_edit", "do_rename", "do_move",
             "do_copy", "do_delete", "do_mkdir", "record_project_note",
             "record_project_file", "project_summary"]:
    check(f"{name}()", callable(getattr(ga, name, None)), True)

print("\n-- RECALL round trip --")
ga.save_json(ga.MEMORY_FILE, {"core": ["Builds a Godot factory sim"],
                              "archival": ["Two 2560x1440 monitors side by side.",
                                           "Prefers the fish shell."]})
check("finds an archived fact", "2560" in ga.search_memory("monitor"), True)
check("an unmatched query still returns the small archive",
      "2560" in ga.search_memory("penguins"), True)
# The "needs a term" guard only applies once the archive is too big to show whole.
ga.save_json(ga.MEMORY_FILE, {"core": [], "archival":
                             [f"Padding fact {i} about something." * 3 for i in range(150)]})
check("a large archive still needs a term",
      ga.search_memory("a of"), "[Give RECALL a search term.]")
ga.save_json(ga.MEMORY_FILE, {"core": ["Builds a Godot factory sim"],
                              "archival": ["Two 2560x1440 monitors side by side.",
                                           "Prefers the fish shell."]})
check("a small archive is inline rather than hidden",
      "2560" in ga.build_system_prompt("monitors"), True)
check("  ...and is labelled as the whole of it",
      "nothing withheld" in ga.build_system_prompt("monitors"), True)

print("\n-- REMEMBER round trip --")
left = ga.store_memories("Noted.\n[REMEMBER: Always uses tabs in GDScript.]\nDone.")
check("tag stripped from the reply", "REMEMBER" in left, False)
check("fact stored in core", "Always uses tabs in GDScript." in ga.load_memory()["core"], True)
check("and reaches the prompt", "Always uses tabs" in ga.build_system_prompt(), True)

print("\n-- PROJECT note round trip, with no file ever written --")
left = ga.store_project_notes("Right.\n[PROJECT: Belts move left to right, never diagonally.]")
check("tag stripped", "PROJECT:" in left, False)
note = ga.project_note_path()
check("written to the vault", "left to right" in note.read_text(), True)
check("project registered in the ledger", str(project) in ga.load_projects()["projects"], True)
check("REACHES THE PROMPT (was silently lost)",
      "left to right" in ga.build_system_prompt(), True)

print("\n-- and still works once files exist alongside it --")
(project / "belt.gd").write_text("extends Node\n" + "var speed := 1.0\n" * 20)
ga.record_project_file(str(project / "belt.gd"))
summary = ga.project_summary()
check("note kept", "left to right" in summary, True)
check("file listed", "belt.gd" in summary, True)

print("\n-- EVOLVE round trip --")
left = ga.store_growth("Fine.\n[EVOLVE: TRAIT: Blunt when it matters.]")
check("tag stripped", "EVOLVE" in left, False)
check("trait recorded", "Blunt when it matters." in ga.load_character()["learned_traits"], True)
check("and reaches the prompt", "Blunt when it matters" in ga.build_system_prompt(), True)

print("\n-- skills round trip --")
check("saved", ga.save_skill("belt-check | verify a belt | read it, check for speed"),
      "Skill 'belt-check' saved.")
check("listed", "belt-check" in ga.all_skills(), True)
check("loaded", "read it, check for speed" in ga.use_skill("belt-check"), True)
check("unknown skill is explained", ga.use_skill("nope").startswith("[No skill named"), True)

print("\n-- HISTORY round trip --")
cid = ga.new_chat(); ga.title_chat(cid, "Belt handshake")
ga.save_chat(cid, [{"role": "assistant", "content": "Two-phase: belt reserves, machine confirms."}])
check("finds it", "Two-phase" in ga.search_chats("belt handshake", "other"), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
