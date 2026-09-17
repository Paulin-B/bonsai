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

box = Path(tempfile.mkdtemp(prefix="bonsai-syntax-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)
ga.save_trusted([str(box)])

def wrote(name, text):
    path = box / name
    path.write_text(text)
    return ga.check_syntax(path)

print("\n-- the two real failures from the live run --")
# Verbatim shapes of what rooms.py and parser.py actually contained.
unterminated = 'ROOMS = {\n    "vault": Room(\n        description="Gold glitters.\n    ),\n}\n'
check("an unterminated string is caught", bool(wrote("rooms.py", unterminated)), True)
check("and it says where", "line" in wrote("rooms.py", unterminated), True)
bad_dict = "DIRS = {\n    'n': 'north', 'north', 'northeast',\n}\n"
check("a malformed dict literal is caught", bool(wrote("parser.py", bad_dict)), True)

print("\n-- good files are left alone --")
check("valid python", wrote("fine.py", "def main():\n    return 42\n"), "")
check("valid json", wrote("a.json", '{"a": [1, 2], "b": null}'), "")
check("valid toml", wrote("a.toml", 'name = "x"\n[table]\nn = 1\n'), "")
check("valid yaml", wrote("a.yaml", "name: x\nitems:\n  - one\n  - two\n"), "")
check("valid commonjs", wrote("a.cjs", "const x = 1;\nmodule.exports = x;\n"), "")
check("valid es module", wrote("a.mjs", "export const x = 1;\n"), "")

print("\n-- broken files of every checked kind --")
check("python", bool(wrote("b.py", "def f(:\n  pass\n")), True)
check("json", bool(wrote("b.json", '{"a": 1,}')), True)
check("toml", bool(wrote("b.toml", "name = \n")), True)
check("yaml", bool(wrote("b.yaml", "a: [1, 2\nb: 3\n")), True)
check("javascript", bool(wrote("b.js", "function f( { return 1; }\n")), True)

print("\n-- a .js using import is module code, not a syntax error --")
check("top-level import passes", wrote("mod.js", "import x from './x.js';\nconsole.log(x);\n"), "")
check("top-level export passes", wrote("exp.js", "export function go() { return 1; }\n"), "")
check("and the temporary file is cleaned up",
      [f.name for f in box.iterdir() if "bonsai-check" in f.name], [])
check("a genuinely broken module is still caught",
      bool(wrote("badmod.js", "import x from './x.js';\nfunction f( {\n")), True)

print("\n-- anything without a parser installed is silently skipped --")
for name in ("a.html", "a.css", "shader.frag", "a.gd", "a.rs", "a.md", "a.txt"):
    check(f"{name} is not checked", wrote(name, "this is (((not parseable"), "")

print("\n-- checking never executes what was written --")
marker = box / "SHOULD_NOT_EXIST"
wrote("evil.py", f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n")
wrote("evil.js", f"require('fs').writeFileSync({str(marker)!r}, 'ran');\n")
wrote("evil.mjs", f"import fs from 'fs';\nfs.writeFileSync({str(marker)!r}, 'ran');\n")
check("nothing ran", marker.exists(), False)
check("valid code that would be destructive still passes the check",
      wrote("evil.py", "import shutil\nshutil.rmtree('/')\n"), "")

print("\n-- odd inputs do not turn a good write into a failure --")
check("an empty file is not an error", wrote("empty.py", ""), "")
check("whitespace only is not an error", wrote("blank.py", "   \n\n"), "")
check("a file that isn't there", ga.check_syntax(box / "missing.py"), "")
check("something enormous is skipped",
      ga.check_syntax(box / "huge.py", "x" * (ga.SYNTAX_CHECK_MAX_BYTES + 1)), "")

print("\n-- WRITE reports it, and says it is not done --")
out = ga.do_write(str(box / "w.py"), "def broken(:\n    pass\n")
check("the write still happened", (box / "w.py").exists(), True)
check("and is reported as written", out.startswith("Wrote "), True)
check("with a warning that it does not parse", "does not parse" in out, True)
check("saying the file is on disk anyway", "IS on disk" in out, True)
check("that this is not finished work", "NOT finished work" in out, True)
check("and what to do about it", "EDIT" in out, True)
out = ga.do_write(str(box / "w2.py"), "def fine():\n    return 1\n")
check("a good write stays quiet", "does not parse" in out, False)

print("\n-- EDIT reports it too: splicing breaks files more often than writing --")
target = box / "e.py"
target.write_text("def one():\n    return 1\n\n\ndef two():\n    return 2\n")
out = ga.do_edit(str(target), "    return 1\n|||\n    return (1\n")
check("the edit applied", out.startswith("Edited "), True)
check("and the breakage is reported", "does not parse" in out, True)
target.write_text("def one():\n    return 1\n")
out = ga.do_edit(str(target), "    return 1\n|||\n    return 99\n")
check("a good edit stays quiet", "does not parse" in out, False)

print("\n-- a broken file cannot be evidence that a task is done --")
ga.save_tasks([{"id": 1, "text": "write the parser", "done": False, "evidence": ""}], 2)
broken = box / "evidence.py"
broken.write_text("class Parser:\n    def go(:\n" + "# padding\n" * 20)
path, why = ga.verify_evidence(str(broken))
check("refused", path, None)
check("because it does not parse", "does not parse" in why, True)
check("and it says a file that won't load isn't finished", "not finished work" in why, True)
check("the file is big enough to have passed the old gate",
      broken.stat().st_size > ga.MIN_EVIDENCE_BYTES, True)

broken.write_text("class Parser:\n    def go(self):\n        return 1\n" + "# padding\n" * 20)
path, why = ga.verify_evidence(str(broken))
check("fixing it lets the task close", why, None)
check("and hands back the path", path, broken)

print("\n-- the existing gates still work --")
(box / "stub.py").write_text("x=1\n")
path, why = ga.verify_evidence(str(box / "stub.py"))
check("a stub is still too small", "stub" in (why or ""), True)
path, why = ga.verify_evidence(str(box / "nothere.py"))
check("a missing file is still missing", "does not exist" in (why or ""), True)

print("\n-- a tool call that came apart is never written to a file --")
# Taken from a real .gd file that sat corrupted in a project for five days: its first
# line was the name of a tool argument, and it carried a bare ||| with the function it
# was meant to replace duplicated around it.
CORRUPT = ("content=\nextends Node2D\nclass_name FlowFieldManager\n\n"
           "func generate_field(goal):\n\t# comments only\n|||\n"
           "func generate_field(goal):\n\tvar d = {}\n")
why = ga.mangled_payload(CORRUPT)
check("it is recognised as malformed", bool(why), True)
check("and says which part gave it away", "content=" in why, True)

before = box / "keep.gd"
before.write_text("extends Node\nvar speed := 90.0\n")
out = ga.do_write(str(before), CORRUPT)
check("the write is refused", out.startswith("[Refused:"), True)
check("it says nothing was written", "NOTHING was written" in out, True)
check("and the file on disk is untouched",
      before.read_text(), "extends Node\nvar speed := 90.0\n")

check("an EDIT payload sent to WRITE is caught",
      "EDIT separator" in ga.mangled_payload("old text\n|||\nnew text"), True)
check("so is a faked tool-result header",
      "tool-result header" in ga.mangled_payload(
          "--- READ_FILE (/a.gd) RESULT ---\n1| extends Node\n"), True)
for argument in ("path=", "old_text=", "new_text=", "text="):
    check(f"'{argument}' alone on line 1 is caught",
          bool(ga.mangled_payload(f"{argument}\nextends Node\n")), True)

print("\n-- and ordinary files are not refused --")
for label, body in [
    ("plain code", "extends Node\nvar x = 1\n"),
    ("a markdown table", "| a | b |\n|---|---|\n| 1 | 2 |\n"),
    ("a python dict named content", "content = {}\nprint(content)\n"),
    ("a shell pipe", "cat a | grep b | wc -l\n"),
    ("text mentioning |||", "The separator is ||| when editing.\n"),
    ("a docstring with dashes", '"""--- section ---"""\nx = 1\n'),
]:
    check(f"{label} is allowed", ga.mangled_payload(body), "")
ok_file = box / "fine.gd"
out = ga.do_write(str(ok_file), "extends Node\nvar speed := 90.0\n")
check("and a normal write still happens", out.startswith("Wrote "), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
