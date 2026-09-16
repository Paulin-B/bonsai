import importlib.util, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("ga", str(Path(__file__).resolve().parent.parent / "gui_assistant.py"))
ga = importlib.util.module_from_spec(spec); spec.loader.exec_module(ga)
box = Path(tempfile.mkdtemp(prefix="bonsai-syn2-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE",
          "DEBUG_LOG_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "max_tool_steps": 5,
                  "temperature": 1.0, "tool_temperature": 0.5})
FACTS = ["Sam's GPU 0 is an RTX 3060 and GPU 1 an RTX 3080 Ti.",
         "Two 2560x1440 monitors side by side.",
         "Prefers the fish shell.",
         "Runs CachyOS with Hyprland."]
app = ga.QApplication(sys.argv)

def turn(prompt):
    cfg = dict(ga.settings()); cfg["chat_id"] = "sim"
    w = ga.Worker(prompt, False, [], cfg)
    out, trace = {}, []
    w.tool_ran.connect(trace.append)
    w.finished.connect(lambda r, t, e, u: out.update(reply=r))
    w.failed.connect(lambda m: out.update(reply="ERROR " + m[:90]))
    w.run()
    return trace, out.get("reply", "")

CASES = [("What graphics card am I using?", "3060"),
         ("Tell me about my display setup.", "2560"),
         ("What terminal do I use?", "fish"),
         ("What operating system am I on?", "CachyOS")]

for label, archival in [("SMALL archive (inline in the prompt)", FACTS),
                        ("LARGE archive (must be searched)",
                         [f"Padding fact {i}: an unrelated stored detail, deliberately verbose so the archive genuinely exceeds the inline budget." for i in range(200)] + FACTS)]:
    ga.save_json(ga.MEMORY_FILE, {"core": [], "archival": archival})
    inline = "3060" in ga.build_system_prompt()
    print(f"\n=== {label} — {len(archival)} facts, inline={inline} ===")
    good = 0
    for prompt, needle in CASES:
        trace, reply = turn(prompt)
        hit = needle in reply
        good += hit
        used = "RECALL" if any(t.startswith("RECALL") for t in trace) else "none"
        print(f"  {prompt:36} tool={used:6} correct={hit}")
        if not hit:
            print(f"      says: {' '.join(reply.split())[:100]}")
    print(f"  -> {good}/{len(CASES)} answered from memory")
