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

box = Path(tempfile.mkdtemp(prefix="bonsai-voice-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
app = ga.QApplication(sys.argv)

def drive(replies, config=None):
    """Record the temperature and tool-availability of every request in a turn."""
    cfg = {"max_tool_steps": 10, "native_tools": True,
           "temperature": 1.5, "tool_temperature": 0.5}
    cfg.update(config or {})
    worker = ga.Worker("do the thing", False, [], cfg)
    calls, seq = [], list(replies)
    def ask_model(system, prompt, image=None, temperature=None, with_tools=True,
                  stream=False):
        calls.append((temperature, with_tools))
        return (seq.pop(0) if seq else replies[-1], None)
    worker.ask_model = ask_model
    worker.run_tool = lambda name, arg: "Wrote 900 characters to '/t/a.gd'."
    worker.model_name = lambda: "fake"
    out = {}
    worker.finished.connect(lambda r, t, e, u: out.update(reply=r, trace=t))
    worker.failed.connect(lambda m: out.update(error=m))
    worker.run()
    return calls, out

print("\n-- a tool-using turn --")
calls, out = drive(["[TOOL: FILE_OP: WRITE | /t/a.gd | body]", "Written, and it's tidy.",
                    "Written it - and it came out rather tidy, if I say so myself."])
check("no error", "error" not in out, True)
check("loop steps run steady, with tools",
      [c for c in calls[:-1]], [(0.5, True), (0.5, True)])
check("the closing reply runs at the voice temperature", calls[-1][0], 1.5)
check("  ...and cannot call another tool", calls[-1][1], False)
check("the spoken version is what you see", out["reply"].startswith("Written it"), True)

print("\n-- a plain conversational turn --")
calls, out = drive(["Hello, how can I help?", "Hello! What are we breaking today?"])
check("first pass steady", calls[0], (0.5, True))
check("answer re-spoken in voice", calls[-1], (1.5, False))
check("voice version wins", out["reply"], "Hello! What are we breaking today?")

print("\n-- identical temperatures skip the extra call --")
calls, out = drive(["[TOOL: FILE_OP: WRITE | /t/a.gd | body]", "Done."],
                   {"temperature": 0.5, "tool_temperature": 0.5})
check("only the loop calls are made", len(calls), 2)
check("all at the one temperature", {c[0] for c in calls}, {0.5})
check("reply kept as generated", out["reply"], "Done.")

print("\n-- an empty voice pass falls back to the steady reply --")
calls, out = drive(["[TOOL: FILE_OP: WRITE | /t/a.gd | body]", "Steady answer.", "   "])
check("empty regeneration discarded", out["reply"], "Steady answer.")

print("\n-- running out of steps still speaks in voice, without tools --")
calls, out = drive(["[TOOL: FILE_OP: WRITE | /t/a.gd | body]"] * 4,
                   {"max_tool_steps": 3})
check("wrap-up is the last call", calls[-1], (1.5, False))

print("\n-- samplers come from settings, not hardcoded --")
src = Path(ga.__file__ if hasattr(ga, "__file__") else "").name
from bonsai_under_test import APP
text = APP.read_text()
for gone in ['"top_p": 0.95', '"top_k": 64', '"presence_penalty": 1.0']:
    check(f"Gemma-era {gone.split(':')[0].strip(chr(34))} no longer hardcoded",
          gone in text, False)
check("top_p configurable", ga.DEFAULTS["top_p"], 0.8)
check("top_k configurable", ga.DEFAULTS["top_k"], 20)
check("presence penalty defaults off", ga.DEFAULTS["presence_penalty"], 0.0)
check("tool temperature default", ga.DEFAULTS["tool_temperature"], 0.5)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
