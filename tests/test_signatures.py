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

box = Path(tempfile.mkdtemp(prefix="bonsai-sig-")); (box / ".git").mkdir()
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)
ga.save_trusted([str(box)])
ROOTS = [str(box)]

print("\n-- reading python signatures exactly --")
sigs = ga.function_arity("x.py", """
def plain(a, b): pass
def defaulted(a, b=1, c=2): pass
def variadic(a, *rest): pass
def kw(a, **opts): pass
class Thing:
    def method(self, a): pass
    @staticmethod
    def static(a, b): pass
""")
check("required only", sigs["plain"][:2], (2, 2))
check("defaults widen the range", sigs["defaulted"][:2], (1, 3))
check("*args is unbounded", sigs["variadic"][1], ga.UNBOUNDED)
check("**kwargs too", sigs["kw"][1], ga.UNBOUNDED)
check("a method knows it is one", sigs["method"][2], True)
check("counting self", sigs["method"][:2], (2, 2))

print("\n-- reading call sites --")
calls = ga.function_calls("x.py", "spawn(1, 2)\nobj.tick(3)\nspawn(*items)\nspawn(**opts)\nspawn(a=1)\n")
by_name = {(n, attr): count for n, _, count, attr in calls}
check("a plain call", by_name[("spawn", False)], 1)
check("a method call is marked", ("tick", True) in by_name, True)
check("keywords count as arguments", by_name[("spawn", False)], 1)
check("splatted calls are skipped, not guessed",
      len([c for c in calls if c[0] == "spawn"]), 2)

print("\n-- the case you asked for: a signature change with callers left behind --")
(box / "caller.py").write_text("from engine import spawn\n\n\ndef go():\n    return spawn(3)\n")
lib = box / "engine.py"
BEFORE = "def spawn(count):\n    return [0] * count\n"
AFTER = "def spawn(count, kind):\n    return [0] * count\n"
lib.write_text(BEFORE)
warn = ga.signature_breakage(lib, BEFORE, AFTER, ROOTS)
check("it warns", bool(warn), True)
check("naming the caller and line", "caller.py:5" in warn, True)
check("what it passes now", "spawn(1 arg)" in warn, True)
check("and what it needs", "now takes 2" in warn, True)

print("\n-- and stays quiet when it should --")
check("adding an optional parameter breaks nobody",
      ga.signature_breakage(lib, BEFORE, "def spawn(count, kind=1):\n    return []\n", ROOTS), "")
check("changing only the body",
      ga.signature_breakage(lib, BEFORE, "def spawn(count):\n    return list(range(count))\n", ROOTS), "")
check("renaming a parameter",
      ga.signature_breakage(lib, BEFORE, "def spawn(n):\n    return [0] * n\n", ROOTS), "")
check("a brand new file has no baseline",
      ga.signature_breakage(lib, "", AFTER, ROOTS), "")
check("a function nothing calls",
      ga.signature_breakage(lib, "def unused(a):\n    pass\n",
                            "def unused(a, b):\n    pass\n", ROOTS), "")

print("\n-- a caller that was ALREADY wrong is not blamed on this change --")
(box / "wrong.py").write_text("from engine import spawn\n\n\ndef go():\n    return spawn(1, 2, 3)\n")
check("pre-existing breakage stays silent",
      "wrong.py" in ga.signature_breakage(lib, BEFORE, AFTER, ROOTS), False)
(box / "wrong.py").unlink()

print("\n-- ambiguous names are left alone rather than guessed --")
two = "def spawn(a):\n    pass\n\n\nclass B:\n    def spawn(self, a, b):\n        pass\n"
check("a name defined twice is not tracked", "spawn" in ga.function_arity("x.py", two), False)

print("\n-- methods: self is never passed at the call site --")
(box / "uses_method.py").write_text("def run(thing):\n    return thing.tick(1)\n")
mlib = box / "engine2.py"
M_BEFORE = "class E:\n    def tick(self, dt):\n        return dt\n"
M_AFTER = "class E:\n    def tick(self, dt, scale):\n        return dt\n"
mlib.write_text(M_BEFORE)
warn = ga.signature_breakage(mlib, M_BEFORE, M_AFTER, ROOTS)
check("a method change is caught", "uses_method.py:2" in warn, True)
check("and the count excludes self", "now takes 2" in warn, True)

print("\n-- GLSL, which is the reason this matters for shaders --")
shaders = box / "shaders"; shaders.mkdir()
(shaders / "use.frag").write_text(
    "#version 120\nvec3 tint(vec3 c, float amt);\nvoid main() {\n"
    "    gl_FragColor.rgb = tint(vec3(1.0), 0.5);\n}\n")
helper = shaders / "lib.glsl"
G_BEFORE = "vec3 tint(vec3 c, float amt) {\n    return c * amt;\n}\n"
G_AFTER = "vec3 tint(vec3 c, float amt, float bias) {\n    return c * amt + bias;\n}\n"
helper.write_text(G_BEFORE)
check("glsl arity is read", ga.function_arity(helper, G_BEFORE)["tint"][:2], (2, 2))
warn = ga.signature_breakage(helper, G_BEFORE, G_AFTER, ROOTS)
check("a shader helper gaining a parameter is caught", "use.frag" in warn, True)
check("with the call it broke", "tint(2 args)" in warn, True)

print("\n-- javascript --")
js = box / "game.js"
J_BEFORE = "function spawn(count) { return []; }\nspawn(3);\n"
J_AFTER = "function spawn(count, kind) { return []; }\nspawn(3);\n"
js.write_text(J_BEFORE)
check("js arity", ga.function_arity(js, J_BEFORE)["spawn"][:2], (1, 1))
check("a call in the same file is checked too",
      "game.js:2" in ga.signature_breakage(js, J_BEFORE, J_AFTER, ROOTS), True)
check("a js default parameter widens the range",
      ga.function_arity(js, "function f(a, b = 2) {}")["f"][:2], (1, 2))
check("a rest parameter is unbounded",
      ga.function_arity(js, "function f(a, ...rest) {}")["f"][1], ga.UNBOUNDED)

print("\n-- languages whose parameters cannot be read confidently are skipped --")
check("typescript is not arity-checked, because it marks defaults 'required'",
      ga.function_arity(box / "a.ts", "function f(a: number, b: string = 'x') {}"), {})
check("lua varargs are unbounded",
      ga.function_arity(box / "a.lua", "function f(a, ...) end")["f"][1], ga.UNBOUNDED)
check("go variadics are unbounded",
      ga.function_arity(box / "a.go", "package m\nfunc f(a int, b ...int) {}")["f"][1],
      ga.UNBOUNDED)
check("gdscript defaults widen the range",
      ga.function_arity(box / "a.gd", "func f(a, b = 2):\n\tpass\n")["f"][:2], (1, 2))

print("\n-- backups are never searched: every write leaves a copy of the old file --")
lib.write_text(BEFORE)
ga.do_write(str(lib), AFTER)            # makes a dated backup of engine.py
ga.do_write(str(box / "caller.py"), (box / "caller.py").read_text())
backups = [f for f in ga.code_files([str(box)])
           if str(Path(ga.BACKUP_DIR).resolve()) in str(Path(f).resolve())]
check("no backup copy is indexed", backups, [])
lib.write_text(BEFORE)
warn = ga.signature_breakage(lib, BEFORE, AFTER, ROOTS)
check("so the warning points at the real file",
      "backup" in warn.lower(), False)
check("and still finds the genuine caller", "caller.py:5" in warn, True)

print("\n-- it reaches the model through WRITE and EDIT --")
lib.write_text(BEFORE)
out = ga.do_write(str(lib), AFTER)
check("WRITE carries the warning", "left callers behind" in out, True)
check("naming the file and line", "caller.py:5" in out, True)
lib.write_text(BEFORE)
out = ga.do_edit(str(lib), "def spawn(count):\n|||\ndef spawn(count, kind):\n")
check("EDIT carries it too", "left callers behind" in out, True)
lib.write_text(BEFORE)
out = ga.do_write(str(lib), "def spawn(count):\n    return list(range(count))\n")
check("an innocent change stays quiet", "WARNING" in out, False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
