import importlib.util, tempfile, sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-notes-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
vault = box / "Vault"; vault.mkdir()
proj = box / "Factory Sim"; (proj / "src").mkdir(parents=True)
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault)})
ga.save_trusted([str(proj)])

print("\n-- notes are markdown in the vault --")
f = proj / "src/flow_field.gd"; f.write_text("extends Node\n" + "x" * 500)
ga.record_project_file(str(f))
ga.project_summary()
note = ga.project_note_path()
check("note lands under Bonsai/ in the vault", note, vault / "Bonsai" / "Factory Sim.md")
check("file exists on disk", note.is_file(), True)
body = note.read_text()
check("has a markdown heading", body.count("# Factory Sim"), 1)
check("generated block lists the real file", "src/flow_field.gd" in body, True)

print("\n-- the model's notes append to it --")
ga.record_project_note("Grid is 64x64 tiles, origin top-left.")
ga.record_project_note("Bots reserve tiles through [[grid_reservation]], never directly.")
body = note.read_text()
check("both notes written", body.count("- Grid is 64x64") + body.count("- Bots reserve"), 2)
check("wikilink preserved", "[[grid_reservation]]" in body, True)
ga.record_project_note("Grid is 64x64 tiles, origin top-left.")
check("duplicate note not appended twice", note.read_text().count("- Grid is 64x64"), 1)
check("notes reach the system prompt", "Grid is 64x64" in ga.build_system_prompt(), True)

print("\n-- your hand edits survive regeneration --")
edited = note.read_text() + "\n## My own section\nDon't use A* here, the flow field is the point.\n"
ga.save_project_note(edited)
g = proj / "src/grid_reservation.gd"; g.write_text("extends Node\n" + "y" * 400)
ga.record_project_file(str(g))
ga.project_summary()
body = note.read_text()
check("hand-written section kept", "Don't use A* here" in body, True)
check("model notes kept", "Grid is 64x64" in body, True)
check("new file appears in the generated block", "grid_reservation.gd" in body, True)
check("generated block not duplicated", body.count(ga.NOTE_AUTO_START), 1)

print("\n-- the generated block tracks reality --")
g.unlink()
ga.project_summary()
body = note.read_text()
check("deleted file drops out", "grid_reservation.gd" in body.split(ga.NOTE_AUTO_END)[0], False)
check("  ...but a note referring to it is untouched", "[[grid_reservation]]" in body, True)

print("\n-- no vault configured: falls back, does not crash --")
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})
check("no note path", ga.project_note_path(), None)
check("recording still works", ga.record_project_note("Stored in JSON instead."), True)
check("  ...into the JSON store",
      "Stored in JSON instead." in ga.load_projects()["projects"][str(proj)]["notes"], True)
check("summary still builds", "Factory Sim" in ga.project_summary(), True)

print("\n-- the pane --")
app = ga.QApplication(sys.argv)
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault)})
ga.Bonsai.start_docker = lambda self: None
w = ga.Bonsai(); w.resize(1400, 760); w.show()   # a child of an unshown window is never 'visible'
for _ in range(4): app.processEvents()
check("pane visible by default", w.notes_pane.isVisible(), True)
check("pane shows the note", "Grid is 64x64" in w.notes_editor.toPlainText(), True)
check("path shown", "Factory Sim.md" in w.notes_path_label.text(), True)
w.notes_editor.setPlainText(w.notes_editor.toPlainText() + "\n- Edited in Bonsai.\n")
w.save_notes()
check("saving from the pane writes the file", "Edited in Bonsai." in ga.read_project_note(), True)
w.toggle_notes()
check("toggle hides it", w.notes_pane.isVisible(), False)
check("  ...and is remembered", w.settings["notes_visible"], False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
