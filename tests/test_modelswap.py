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

from bonsai import worker as W
from bonsai.store import match_model

print("-- /model picks by what was typed --")
labels = ["Local", "Remote", "Local · qwen3-30b", "Local · qwen3-8b"]
check("exact name", match_model("remote", labels), (1, []))
check("a model under a server, by its own name", match_model("qwen3-8b", labels), (3, []))
check("one substring match", match_model("30b", labels), (2, []))
check("ambiguous lists the candidates", match_model("qwen", labels), (None, [2, 3]))
check("no match", match_model("llama", labels), (None, []))
check("nothing typed lists everything", match_model("", labels), (None, [0, 1, 2, 3]))
check("exact beats substring", match_model("local", labels), (0, []))

print("\n-- tool results reach the debug log --")
box = Path(tempfile.mkdtemp(prefix="bonsai-log-"))
W.DEBUG_LOG_FILE = box / "debug.log"
w = W.Worker("hi", False, [], {})
w.log_tool("LIST_DIR", "/media", "[Directory not found: /media]")
text = (box / "debug.log").read_text()
check("the ask and the answer are together", "LIST_DIR" in text and "Directory not found" in text, True)
w.log_tool("READ_FILE", "/big", "x" * 5000)
check("long results are cut, and say so", "3000 more characters not logged" in (box / "debug.log").read_text(), True)
(box / "debug.log").unlink()
w = W.Worker("hi", False, [], {"log_tool_results": False})
w.log_tool("LIST_DIR", "/media", "secret")
check("can be switched off", (box / "debug.log").exists(), False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
