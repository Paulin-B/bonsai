import sys, importlib.util
from pathlib import Path
from bonsai_under_test import APP, load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

print("\n-- questions that stall an unattended turn --")
for reply in [
    "**Next step:** Writing `item_manager.gd`. Should I proceed?",
    "Should I start by rewriting `navigation/grid_reservation.gd` now?",
    "I'm ready to begin as soon as you confirm the file path.\n\nShall I continue?",
    "**Next step:** Creating the script. Should I place this in `/srv/factory-game/src/agents/bot.gd`?",
]:
    check(f"question: {reply.splitlines()[-1][:44]}", ga.awaits_an_answer(reply), True)

print("  -- statements are left alone --")
for reply in [
    "I wrote the file and marked task 3 done.",
    "Blocked: the folder is read-only, so I stopped.",
    "Done. The flow field now handles weights.\n\n(Next: belts.)",
    "Why does this matter? Because bots would collide otherwise.\nI've handled it in code.",
]:
    check(f"not a question: {reply.splitlines()[-1][:40]}", ga.awaits_an_answer(reply), False)

print("\n-- write shrink guard (your 1397 -> 154 clobber) --")
d = Path(__file__).parent / "autocases"; d.mkdir(exist_ok=True)
f = d / "grid_reservation.gd"

def gd(size):
    """Valid GDScript of exactly `size` bytes - a comment is a whole file's worth of
    legal syntax. This block is about how much of a file a write removes, and .gd is
    now parsed for real, so padding it with 'x' told us about the parser instead."""
    return "# " + "x" * (size - 3) + "\n"

f.write_text(gd(1397))
r = ga.do_write(str(f), gd(154))
check("gutting an existing file warns", "WARNING" in r and "most of the file is gone" in r, True)
check("  ...and points at EDIT", "use EDIT instead of WRITE" in r, True)
check("  ...and the write still happened", f.stat().st_size, 154)

f.write_text(gd(1397))
r = ga.do_write(str(f), gd(1300))
check("a normal rewrite is quiet", "WARNING" in r, False)

f.write_text(gd(120))
r = ga.do_write(str(f), gd(10))
check("tiny files don't trip it", "WARNING" in r, False)

r = ga.do_write(str(d / "brand_new.gd"), gd(10))
check("a new file doesn't trip it", "WARNING" in r, False)

print("\n-- unattended flag reaches the worker --")
src = APP.read_text()
# Which methods dispatch unattended, found by walking the source rather than by
# counting a string: counting breaks the moment a legitimate new caller is added,
# which is exactly what it did when self-resumption arrived.
import ast as _ast
_tree = _ast.parse(src)
_dispatches = {}
for _node in _ast.walk(_tree):
    if not isinstance(_node, _ast.FunctionDef):
        continue
    for _call in _ast.walk(_node):
        if (isinstance(_call, _ast.Call) and isinstance(_call.func, _ast.Attribute)
                and _call.func.attr == "dispatch"):
            _flag = next((k.value for k in _call.keywords if k.arg == "unattended"), None)
            _dispatches.setdefault(_node.name, []).append(
                isinstance(_flag, _ast.Constant) and _flag.value is True)
check("auto mode dispatches unattended", all(_dispatches.get("maybe_continue", [])), True)
check("so does self-resumption", all(_dispatches.get("maybe_resume", [])), True)
check("nothing dispatches unattended by accident",
      {name for name, flags in _dispatches.items() if any(flags)},
      {"on_auto_clicked", "maybe_continue", "maybe_resume"})
check("pressing Auto starts an unattended round",
      all(_dispatches.get("on_auto_clicked", [])), True)
check("send() stays attended", "self.dispatch(prompt, self.vision_box.isChecked(), retry=False)" in src, True)
check("retry stays attended", "self.dispatch(self.last_prompt, self.last_vision, retry=True)" in src, True)
check("worker reads the flag", 'self.config.get("unattended", False)' in src, True)

print("\n-- each nudge can fire only once --")
check("every nudge has its own one-shot flag",
      [flag for flag in ["corrected", "pressed", "reminded", "searched",
                         "recalled"] if f"{flag} = True" not in src],
      [])
for flag in ["reminded = True", "pressed = True", "corrected = True",
             "searched = True"]:
    check(f"{flag} set before continue", src.count(flag), 1)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
