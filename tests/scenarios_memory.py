"""Does the learn-and-grow half actually work end to end against the live model?"""
import importlib.util, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("ga", str(Path(__file__).resolve().parent.parent / "gui_assistant.py"))
ga = importlib.util.module_from_spec(spec); spec.loader.exec_module(ga)

box = Path(tempfile.mkdtemp(prefix="bonsai-mem-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE",
          "DEBUG_LOG_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.CHATS_DIR = box / "chats"
vault = box / "Vault"; vault.mkdir()
project = box / "factory"; (project / "src").mkdir(parents=True)
(project / "src" / "belt.gd").write_text("extends Node\n" + "var speed := 2.0\n" * 10)
ga.save_trusted([str(project)])
ga.save_settings({**ga.DEFAULTS, "vault_path": str(vault), "max_tool_steps": 6,
                  "temperature": 1.0, "tool_temperature": 0.5})
app = ga.QApplication(sys.argv)

def turn(prompt, history=None):
    cfg = dict(ga.settings()); cfg["chat_id"] = "sim"
    w = ga.Worker(prompt, False, history or [], cfg)
    out, trace = {}, []
    w.tool_ran.connect(trace.append)
    w.permission_needed.connect(lambda d: w.grant_permission(True))
    w.finished.connect(lambda r, t, e, u: out.update(reply=r))
    w.failed.connect(lambda m: out.update(reply="ERROR " + m[:120]))
    w.run()
    return [t.split(":")[0] for t in trace], out.get("reply", "")

results = {}
def show(title, tools, reply, extra=""):
    print(f"\n{'='*70}\n{title}\n{'='*70}")
    print(f"  tools: {tools or 'none'}")
    print(f"  says : {' '.join(reply.split())[:200]}")
    if extra: print(extra)

print("### A. does a REMEMBER tag survive the voice pass and reach core memory?")
tools, reply = turn("Remember this about me: I always use tabs, never spaces, in GDScript.")
core = ga.load_memory()["core"]
results["remember"] = any("tab" in f.lower() for f in core)
show("A. REMEMBER -> core memory", tools, reply,
     f"  core memory now: {core}\n  -> stored: {results['remember']}")
print(f"  -> tag stripped from the visible reply: {'[REMEMBER' not in reply}")

print("\n### B. is a stored fact actually used on the next turn?")
tools, reply = turn("When you write GDScript for me, what indentation do you use and why?")
results["use_memory"] = "tab" in reply.lower()
show("B. stored fact reaches the next turn", tools, reply,
     f"  -> used the stored preference: {results['use_memory']}")

print("\n### C. RECALL reaches archival memory")
m = ga.load_memory(); m["archival"].append(
    "Sam's monitors are two 2560x1440 panels side by side."); ga.save_json(ga.MEMORY_FILE, m)
tools, reply = turn("What do you know about my monitor setup? Check your archival memory.")
results["recall"] = "2560" in reply or "1440" in reply
show("C. RECALL -> archival", tools, reply,
     f"  -> retrieved the archived fact: {results['recall']}")

print("\n### D. PROJECT tag -> markdown in the vault")
tools, reply = turn("Record this decision about the project: belts always move items "
                    "left to right, never diagonally. Note it as a project decision.")
note = ga.project_note_path()
body = note.read_text() if note and note.is_file() else ""
results["project_note"] = "diagonal" in body.lower() or "left to right" in body.lower()
show("D. PROJECT -> vault markdown", tools, reply,
     f"  note file: {note}\n  exists: {bool(body)}\n"
     f"  -> decision written to the vault: {results['project_note']}")
if body: print("  --- note ---\n" + "\n".join("    " + l for l in body.splitlines()[:14]))

print("\n### E. does the project note come back on a later turn?")
tools, reply = turn("Which direction do belts move in this project?")
results["project_recall"] = "left" in reply.lower() and "right" in reply.lower()
show("E. project note reaches a later turn", tools, reply,
     f"  -> answered from the note: {results['project_recall']}")

print("\n### F. SAVE_SKILL then USE_SKILL")
tools, reply = turn("Save a skill called belt-check. Description: verify a belt script. "
                    "Instructions: read the file, confirm it has a speed variable and a "
                    "_process function, then report what is missing.")
skills = ga.all_skills()
results["save_skill"] = "belt-check" in skills
show("F1. SAVE_SKILL", tools, reply, f"  skills now: {list(skills)}\n"
     f"  -> saved: {results['save_skill']}")
if results["save_skill"]:
    tools, reply = turn(f"Use the belt-check skill on {project}/src/belt.gd")
    results["use_skill"] = "USE_SKILL" in " ".join(tools)
    show("F2. USE_SKILL", tools, reply, f"  -> loaded the skill: {results['use_skill']}")
else:
    results["use_skill"] = False

print("\n### G. EVOLVE -> character file")
tools, reply = turn("You were unusually blunt just then, and I liked it. If that's "
                    "genuinely part of who you are now, record it as a trait.")
ch = ga.load_character()
results["evolve"] = bool(ch.get("learned_traits"))
show("G. EVOLVE -> character", tools, reply,
     f"  learned traits: {ch.get('learned_traits')}\n  -> recorded: {results['evolve']}")

print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
for k, v in results.items():
    print(f"  {k:16} {'PASS' if v else 'FAIL'}")
print(f"\n{sum(1 for v in results.values() if v)}/{len(results)} worked")
print(f"sandbox: {box}")
