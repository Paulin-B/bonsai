import sys, json, importlib.util, tempfile, shutil
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

# Redirect all storage into a sandbox so the real memory files are untouched.
sandbox = Path(tempfile.mkdtemp(prefix="bonsai-proj-"))
proj = sandbox / "factory-game"; (proj / "src" / "navigation").mkdir(parents=True)
ga.PROJECTS_FILE = sandbox / "projects.json"
ga.TRUSTED_PATHS_FILE = sandbox / "trusted.json"
# Sandbox settings too, or settings() reads the real file - and its default vault_path
# points at a real Obsidian vault, which this test would then write into.
ga.SETTINGS_FILE = sandbox / "settings.json"
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})   # this suite covers the JSON store
ga.save_trusted([str(proj)])

print("\n-- the ledger records what was actually written --")
ff = proj / "src/navigation/flow_field.gd"; ff.write_text("x" * 3206)
gr = proj / "src/navigation/grid_reservation.gd"; gr.write_text("y" * 972)
ga.record_project_file(str(ff)); ga.record_project_file(str(gr))
files = ga.load_projects()["projects"][str(proj)]["files"]
check("both files remembered, relative to the root",
      sorted(files), ["src/navigation/flow_field.gd", "src/navigation/grid_reservation.gd"])

out = ga.project_summary()
check("names the project root", str(proj) in out, True)
check("lists a file with its real size", "src/navigation/flow_field.gd (3206 bytes" in out, True)
check("says it survives chats", "survives across chats" in out, True)

print("\n-- work outside a trusted folder is not project work --")
stray = sandbox / "scratch.txt"; stray.write_text("hi")
ga.record_project_file(str(stray))
check("untrusted path ignored", str(proj) in ga.load_projects()["projects"]
      and "scratch.txt" not in json.dumps(ga.load_projects()), True)
d = proj / "src/items"; d.mkdir()
ga.record_project_file(str(d))
check("folders aren't recorded as work product", "src/items" in ga.load_projects()["projects"][str(proj)]["files"], False)

print("\n-- the ledger cannot lie: it is checked against disk --")
gr.unlink()
out = ga.project_summary()
check("deleted file drops out of the listing", "grid_reservation" in out, False)
check("  ...and is pruned from storage",
      sorted(ga.load_projects()["projects"][str(proj)]["files"]), ["src/navigation/flow_field.gd"])
check("the surviving file is still listed", "flow_field.gd (3206 bytes" in out, True)

print("\n-- project notes, via the [PROJECT: ...] tag --")
reply = ("Set the grid up.\n[PROJECT: Grid is 64x64 tiles, origin top-left.]\n"
         "[PROJECT: Bots reserve tiles through GridReservation, never directly.]\nDone.")
cleaned = ga.store_project_notes(reply)
check("tags stripped from the visible reply", "PROJECT:" in cleaned, False)
check("reply text kept", cleaned.startswith("Set the grid up.") and cleaned.endswith("Done."), True)
notes = ga.load_projects()["projects"][str(proj)]["notes"]
check("both notes stored", len(notes), 2)
check("note content", notes[0], "Grid is 64x64 tiles, origin top-left.")
ga.store_project_notes("[PROJECT: Grid is 64x64 tiles, origin top-left.]")
check("duplicate note not stored twice", len(ga.load_projects()["projects"][str(proj)]["notes"]), 2)
check("notes appear in the summary", "Grid is 64x64 tiles" in ga.project_summary(), True)

print("\n-- a fresh chat sees all of it (no chat state involved) --")
check("summary is built from disk alone, not history",
      "Bots reserve tiles through GridReservation" in ga.project_summary(), True)

print("\n-- no trusted folders: silent, not crashing --")
ga.save_trusted([])
check("no project, no block", ga.project_summary(), "")
check("recording is a no-op", ga.record_project_file(str(ff)), None)

print("\n-- the stalled turn from your transcript is now detected --")
stalled = ("I'm currently blocked because I don't have a concrete specification.\n\n"
           "**What should my first move be?**\n"
           "1. **Research/Design:** I can start by creating a technical spec.\n"
           "2. **Codebase Exploration:** I can read the flow field code.\n"
           "3. **Direct Implementation:** I can just start writing the base classes.")
check("question above a menu of options is caught", ga.awaits_an_answer(stalled), True)

shutil.rmtree(sandbox)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
