import sys, importlib.util
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

d = Path(__file__).parent / "editcases"; d.mkdir(exist_ok=True)

# Sandbox the config BEFORE any tool runs. Without this, do_edit's reference check
# reads the real trusted-paths file and walks the user's actual project disks.
import tempfile
_box = Path(tempfile.mkdtemp(prefix="bonsai-edit-"))
for _n in ["SETTINGS_FILE", "TRUSTED_PATHS_FILE", "BACKUP_DIR", "TASKS_FILE",
           "MEMORY_FILE", "CHARACTER_FILE", "PROJECTS_FILE"]:
    setattr(ga, _n, _box / _n.lower())
ga.save_settings(ga.DEFAULTS)
ga.save_trusted([str(d)])

def via_native(path, old, new, tool="edit"):
    """Full pipeline: model's structured call -> string -> split -> do_edit."""
    args = ({"path": str(path), "old_text": old, "new_text": new} if tool == "edit"
            else {"action": "EDIT", "path": str(path), "old_text": old, "new_text": new})
    name, arg = ga.native_call_to_tool(tool, args)
    assert name == "FILE_OP", name
    action, first, second = ga.split_file_op(arg)
    assert action == "EDIT", action
    return ga.do_edit(first, second)

def via_text(raw):
    """Full pipeline for the regex fallback: raw model text -> split -> do_edit."""
    name, arg = ga.extract_tool_call(raw)
    assert name == "FILE_OP", (name, arg)
    action, first, second = ga.split_file_op(arg)
    return ga.do_edit(first, second)

print("\n-- indentation survives the round trip (the whole point) --")
f = d / "a.py"
f.write_text("def go():\n    total = 0\n    return total\n")
r = via_native(f, "    total = 0", "    total = 42")
print(f"  -> {r.split(' Previous')[0]}")
check("4-space indent preserved", f.read_text(),
      "def go():\n    total = 42\n    return total\n")

f.write_text("class A:\n    def f(self):\n        if x:\n            return 1\n        return 0\n")
via_native(f, "        if x:\n            return 1", "        if x:\n            return 99")
check("nested indent, multiline", f.read_text(),
      "class A:\n    def f(self):\n        if x:\n            return 99\n        return 0\n")

f.write_text("a = 1\nb = 2\n")
via_native(f, "b = 2", "b = 2\nc = 3", tool="file_op")
check("file_op(action=EDIT) door works", f.read_text(), "a = 1\nb = 2\nc = 3\n")

print("\n-- deleting a line really removes it --")
f.write_text("keep\ndrop me\nkeep\n")
via_native(f, "drop me\n", "")
check("empty replacement leaves no blank line", f.read_text(), "keep\nkeep\n")

print("\n-- refuses rather than guessing --")
f.write_text("x = 1\nx = 1\n")
before = f.read_text()
r = via_native(f, "x = 1", "x = 9")
check("ambiguous match refuses", r.startswith("[Found 2 matches"), True)
check("  ...and changes nothing", f.read_text(), before)

f.write_text("hello\n")
r = via_native(f, "goodbye", "hi")
check("no match refuses", r.startswith("[No match"), True)
check("  ...and changes nothing", f.read_text(), "hello\n")

check("missing separator refuses", ga.do_edit(str(f), "no separator").startswith("[EDIT needs"), True)
check("missing file points at WRITE", "Use FILE_OP WRITE" in ga.do_edit(str(d / "nope.py"), "a\n|||\nb"), True)
check("protected path refused", ga.do_edit("/etc", "a\n|||\nb"), "[Refused: protected system directory.]")
check("sensitive path refused", ga.do_edit("~/.ssh/id_rsa", "a\n|||\nb").startswith("[Refused:"), True)

print("\n-- text fallback, including code full of brackets --")
f.write_text("items = [1, 2]\nprint(items[0])\n")
via_text(f"[TOOL: FILE_OP: EDIT | {f} | print(items[0])\n|||\nprint(items[1])]")
check("']' in code survives the greedy parse", f.read_text(), "items = [1, 2]\nprint(items[1])\n")

f.write_text("a = 1\n")
via_text(f"EDIT: {f} | a = 1 ||| a = 2")
check("inline form still works for one-liners", f.read_text(), "a = 2\n")

print("\n-- line endings and neighbours left alone --")
crlf = d / "crlf.txt"; crlf.write_bytes(b"one\r\ntwo\r\nthree\r\n")
via_native(crlf, "two", "TWO")
check("CRLF file stays CRLF", crlf.read_bytes(), b"one\r\nTWO\r\nthree\r\n")

f.write_text("head\n\n\n    spaced   \ntail\n")
via_native(f, "tail", "TAIL")
check("odd whitespace elsewhere untouched", f.read_text(), "head\n\n\n    spaced   \nTAIL\n")

print("\n-- other FILE_OP actions unchanged by the split refactor --")
check("WRITE payload still stripped", ga.split_file_op("WRITE | /t/a.py |   hi  "),
      ("WRITE", "/t/a.py", "hi"))
check("MKDIR colon form still normalised", ga.split_file_op("MKDIR: /t/new"),
      ("MKDIR", "/t/new", ""))
check("RENAME two args", ga.split_file_op("RENAME | /t/a.py | b.py"),
      ("RENAME", "/t/a.py", "b.py"))

print("\n-- an indentation near-miss is recovered, not refused --")
# Watched live three times: the model adds a leading space that is not in the file.
pair = d / "shader.fsh"
pair.write_text("varying vec2 texcoord;\nvoid main() {\n    gl_FragColor = vec4(texcoord, 0, 1);\n}\n")
r = ga.do_edit(str(pair), " varying vec2 texcoord;\n|||\n varying vec2 uv;\n")
check("a spurious leading space still edits", r.startswith("Edited "), True)
check("and the file gets the un-indented form", "\nvarying vec2 uv;" in "\n" + pair.read_text(), True)
check("and it is told so it can copy verbatim next time", "did not match exactly" in r, True)

indented = d / "block.py"
indented.write_text("class A:\n    def go(self):\n        return 1\n")
r = ga.do_edit(str(indented), "def go(self):\n|||\ndef run(self):\n")
check("missing indentation is recovered too", r.startswith("Edited "), True)
check("keeping the original indent", "    def run(self):" in indented.read_text(), True)

ambiguous = d / "twice.py"
ambiguous.write_text("def a():\n    return 1\n\n\ndef b():\n    return 1\n")
r = ga.do_edit(str(ambiguous), " return 1\n|||\n return 2\n")
check("a near-miss matching twice is still refused", r.startswith("["), True)
check("with nothing written", ambiguous.read_text().count("return 1"), 2)

exact = d / "exact.py"
exact.write_text("x = 1\ny = 2\n")
r = ga.do_edit(str(exact), "x = 1\n|||\nx = 9\n")
check("an exact match says nothing about indentation", "did not match exactly" in r, False)

print("\n-- an edit whose whole point is the indentation --")
# From a real run, twice. A block was left indented into a GDScript class body, and
# every attempt to de-indent it came back "Edited" with the file unchanged: the text
# to find had been typed at the wrong indent, matched after a shift, and the
# replacement was shifted with it - putting the old indentation straight back.
CORRUPT = ("extends Node2D\n\nvar buffer: Array[String] = []\n"
           "\tvar hunger_bar: float = 1.0\n"
           "\tvar hunger_decay_rate: float = 0.01\n"
           "var is_producing: bool = false\n")

def attempt(old, new):
    target = _box / "mother_machine.gd"
    target.write_text(CORRUPT)
    said = ga.do_edit(str(target), f"{old}{ga.EDIT_SEP}{new}")
    return said, target.read_text()

said, after = attempt("\tvar hunger_bar: float = 1.0", "var hunger_bar: float = 1.0")
check("the exact text de-indents the line", "\tvar hunger_bar" in after, False)
check("and it says it edited", said.startswith("Edited "), True)

said, after = attempt("    var hunger_bar: float = 1.0\n    var hunger_decay_rate: float = 0.01",
                      "var hunger_bar: float = 1.0\nvar hunger_decay_rate: float = 0.01")
check("spaces where the file has a tab still finds it", "\tvar hunger_bar" in after, False)
check("and the de-indent is what lands", "\nvar hunger_bar: float = 1.0\n" in after, True)
check("reported as an edit", said.startswith("Edited "), True)

print("\n-- and one that cannot mean anything is not called an edit --")
said, after = attempt("var hunger_bar: float = 1.0", "var hunger_bar: float = 1.0")
check("nothing was written", after, CORRUPT)
check("and it does NOT claim to have edited", said.startswith("Edited "), False)
check("it says nothing changed", said.startswith("[Nothing changed"), True)
check("naming the line", "line 4" in said, True)
check("and what that line's indentation actually is", "1 tab(s)" in said, True)
check("with what to do instead", "exactly as the file has it" in said, True)

print("\n-- ordinary edits are untouched by any of this --")
target = _box / "plain.gd"
target.write_text("extends Node\n\nfunc go():\n\tvar total = 1\n\treturn total\n")
said = ga.do_edit(str(target), f"var total = 1{ga.EDIT_SEP}var total = 42")
check("a needle missing its indent still matches", said.startswith("Edited "), True)
check("and the replacement keeps the file's indentation",
      "\tvar total = 42" in target.read_text(), True)
check("the rest of the file is untouched",
      "\treturn total" in target.read_text(), True)

target.write_text("a = 1\nb = 2\na = 1\n")
said = ga.do_edit(str(target), f"a = 1{ga.EDIT_SEP}a = 99")
check("an ambiguous match is still refused", said.startswith("[Found 2 matches"), True)
check("and nothing was written", target.read_text(), "a = 1\nb = 2\na = 1\n")

print("\n-- the parts that decide it --")
check("same text, different indent, is a re-indent",
      ga.reindents("\tvar x = 1", "var x = 1"), True)
check("same text and same indent is not",
      ga.reindents("\tvar x = 1", "\tvar y = 1"), False)
check("different text is not, however it is indented",
      ga.reindents("\tvar x = 1", "var y = 2"), False)
check("an indent-blind match returns the file's own text",
      ga.indent_blind_match("a\n\tvar x = 1\nb\n", "var x = 1"), "\tvar x = 1")
check("but not when two blocks match",
      ga.indent_blind_match("\tvar x = 1\n  var x = 1\n", "var x = 1"), None)
check("and not when none do",
      ga.indent_blind_match("a\nb\n", "var x = 1"), None)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
