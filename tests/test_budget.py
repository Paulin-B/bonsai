import sys
import tempfile
from pathlib import Path
from bonsai_under_test import APP, load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

def entry(n, size, summary=None):
    # Exactly `size` chars regardless of n - a marker plus padding. Multiplying a
    # variable-width token gave entries 1.5x the intended size once n reached 10.
    marker = f"BODY{n}:"
    return {"name": "READ_FILE", "argument": f"/src/file{n}.gd",
            "result": marker + "x" * max(0, size - len(marker)),
            "summary": summary or f"/src/file{n}.gd (lines 1-400 of 400)"}

print("\n-- under budget, nothing is touched --")
small = [entry(1, 100), entry(2, 100)]
out = ga.render_results(small, 40000)
check("both results in full", out.count("RESULT ---"), 2)
check("no shortening notice", "shortened" in out, False)
check("oldest first", out.index("file1.gd") < out.index("file2.gd"), True)

print("\n-- over budget: newest kept whole, older collapsed --")
big = [entry(n, 30000) for n in range(1, 6)]     # 5 x 30k = 150k chars
out = ga.render_results(big, 40000)
check("stays within budget (plus notices)", len(out) < 45000, True)
check("newest result kept in full", "BODY5:" in out, True)
check("oldest result body dropped", "BODY1:" in out, False)
check("but the oldest call is still listed", "/src/file1.gd" in out, True)
check("  ...with its outcome", "lines 1-400 of 400" in out, True)
check("says how many were shortened", "4 earlier tool result(s) shortened" in out, True)
check("every call still accounted for",
      all(f"file{n}.gd" in out for n in range(1, 6)), True)

print("\n-- a single oversized result is never lost --")
out = ga.render_results([entry(1, 90000)], 40000)
check("kept whole even past budget", len(out) > 89000, True)
check("no spurious shortening notice", "shortened" in out, False)

print("\n-- the worst case from the real settings --")
# 30 steps x max_read_chars 30000 = 900,000 chars, against a 32k-token window.
worst = [entry(n, 30000) for n in range(1, 31)]
unbudgeted = sum(len(f"--- {e['name']} ({e['argument']}) RESULT ---\n{e['result']}")
                 for e in worst)
out = ga.render_results(worst, 40000)
check(f"unbudgeted would be {unbudgeted//1000}k chars (~{unbudgeted//4000}k tokens)",
      unbudgeted > 800000, True)
# One oversized result is always kept whole, so the bound is budget + that result.
check("budgeted output is bounded", len(out) <= 40000 + 30000 + 6000, True)
check("  ...vs unbudgeted", f"{len(out)//1000}k vs {unbudgeted//1000}k chars", 
      f"{len(out)//1000}k vs {unbudgeted//1000}k chars")
check("all 30 calls still named", sum(f"file{n}.gd" in out for n in range(1, 31)), 30)

print("\n-- empty and defaults --")
check("no results", ga.render_results([], 40000), "")
check("setting exists", ga.DEFAULTS["max_result_chars"], 40000)

print("\n-- the prompt no longer duplicates tool docs under native tools --")
saved = dict(ga.settings())
sizes = {}
for native in (True, False):
    ga.save_settings({**saved, "native_tools": native})
    sizes[native] = len(ga.build_system_prompt())
ga.save_settings(saved)
check("native tools prompt is smaller", sizes[True] < sizes[False], True)
check(f"saves {sizes[False]-sizes[True]} chars", sizes[False] - sizes[True] > 2000, True)

print("\n-- message order gives a cacheable prefix --")
src = APP.read_text()
check("system prompt is its own message",
      'messages = [{"role": "system", "content": system_prompt}] + history' in src, True)
check("image precedes the changing text", src.index('"type": "image_url"') <
      src.index('{"type": "text", "text": user_prompt}'), True)
check("prompt caching enabled", '"cache_prompt": True' in src, True)

print("\n-- one file cannot close two tasks --")
box = Path(tempfile.mkdtemp(prefix="bonsai-dupe-"))
ga.TASKS_FILE = box / "tasks.json"
BODY = "extends Node\n" + "".join(
    f"\nvar value_{n} := {n}" for n in range(90))   # valid GDScript, ~1KB
real = box / "item_manager.gd"; real.write_text(BODY)
other = box / "bot_agent.gd"; other.write_text(BODY.replace("value_", "other_"))
ga.handle_task("ADD Data-Oriented Item Manager")
ga.handle_task("ADD Bot Navigation and State Machine")
ga.handle_task(f"DONE 1 | {real}")
r = ga.handle_task(f"DONE 2 | {real}")
check("reusing another task's evidence refused", "already closed task 1" in r, True)
check("  ...names what it already closed", "Data-Oriented Item Manager" in r, True)
check("  ...and the task stays open", ga.load_tasks()[1]["done"], False)
r = ga.handle_task(f"DONE 2 | {other}")
check("its own file closes it", "Marked done" in r, True)

print("\n-- backups no longer collide inside one second --")
ga.BACKUP_DIR = box / "backups"
target = box / "clobbered.gd"
target.write_text("first version" + "a" * 100)
b1 = ga._backup(target)
target.write_text("second version" + "b" * 100)
b2 = ga._backup(target)
check("two backups in the same second are distinct", b1 != b2, True)
check("the first is still readable", "first version" in b1.read_text(), True)
check("the second too", "second version" in b2.read_text(), True)

print("\n-- a failed screen grab degrades to text-only --")
src_text = APP.read_text()
check("capture is wrapped", "The screen capture failed:" in src_text, True)
check("  ...and the turn continues", "you have no image" in src_text, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
