import sys, importlib.util
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

print("\n-- the greedy branch must not hijack other tools --")
for label, text, want in [
    ("READ_FILE of a file named editor.py",
     "[TOOL: READ_FILE: /home/p/editor.py]", ("READ_FILE", "/home/p/editor.py")),
    ("LIST_DIR of an 'edits' folder",
     "[TOOL: LIST_DIR: /home/p/edits]", ("LIST_DIR", "/home/p/edits")),
    ("SEARCH mentioning rewrite",
     "[TOOL: SEARCH: how to rewrite git history]", ("SEARCH", "how to rewrite git history")),
    ("FIND for an edit_ prefixed file",
     "[TOOL: FIND: edit_helpers.py | /home/p]", ("FIND", "edit_helpers.py | /home/p")),
    ("RUN a linter", "[TOOL: RUN: ruff check . | /home/p]", ("RUN", "ruff check . | /home/p")),
    ("plain prose, no call", "I'll take a look at the editor config.", (None, None)),
]:
    check(label, ga.extract_tool_call(text), want)

print("\n-- real calls still parse --")
check("WRITE", ga.extract_tool_call("[TOOL: FILE_OP: WRITE | /t/a.py | x = [1]]"),
      ("FILE_OP", "WRITE | /t/a.py | x = [1]"))
check("EDIT", ga.extract_tool_call("[TOOL: FILE_OP: EDIT | /t/a.py | a\n|||\nb]"),
      ("FILE_OP", "EDIT | /t/a.py | a\n|||\nb"))
check("bare WRITE line", ga.extract_tool_call("WRITE | /t/a.py | hello"),
      ("FILE_OP", "WRITE | /t/a.py | hello"))
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
