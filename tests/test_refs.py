import sys
import tempfile
import time
from pathlib import Path
from bonsai_under_test import APP, load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-refs-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)
shaders = box / "shaders"; shaders.mkdir()
ga.save_trusted([str(box)])

VERT = """#version 120
varying vec2 texcoord;
varying vec4 tint;
void main() {
    gl_Position = ftransform();
    texcoord = (gl_TextureMatrix[0] * gl_MultiTexCoord0).st;
    tint = gl_Color;
}
"""
FRAG = """#version 120
uniform sampler2D texture;
varying vec2 texcoord;
varying vec4 tint;
void main() {
    gl_FragColor = texture2D(texture, texcoord) * tint;
}
"""
(shaders / "gbuffers_basic.vsh").write_text(VERT)
(shaders / "gbuffers_basic.fsh").write_text(FRAG)

print("\n-- tree-sitter is available and reads shaders --")
check("the language pack loaded", ga._ts_parser is not None, True)
check("a shader parses", ga.ts_error_nodes(ga.ts_parse(shaders / "a.frag", FRAG)), [])

print("\n-- only errors a change INTRODUCED are reported --")
broke = FRAG.replace("texture2D(texture, texcoord) * tint;", "texture2D(texture, texcoord * tint;")
check("breaking a line is caught",
      bool(ga.introduced_syntax_error(shaders / "x.frag", FRAG, broke)), True)
check("and located", "line" in ga.introduced_syntax_error(shaders / "x.frag", FRAG, broke), True)
check("an unchanged file reports nothing",
      ga.introduced_syntax_error(shaders / "x.frag", FRAG, FRAG), "")

# The measured GLSL grammar gap: valid GLSL 430 that tree-sitter calls an error.
COMPUTE = """#version 430
layout(local_size_x = 16, local_size_y = 16) in;
layout(rgba32f, binding = 0) uniform image2D img;
void main() {
    imageStore(img, ivec2(gl_GlobalInvocationID.xy), vec4(1.0));
}
"""
check("the grammar really does trip on this valid shader",
      bool(ga.ts_error_marks(shaders / "c.comp", COMPUTE)), True)
check("but editing it elsewhere stays silent, because the error is not new",
      ga.introduced_syntax_error(shaders / "c.comp", COMPUTE,
                                 COMPUTE.replace("vec4(1.0)", "vec4(0.5)")), "")
check("while genuinely breaking it is still caught",
      bool(ga.introduced_syntax_error(shaders / "c.comp", COMPUTE,
                                      COMPUTE.replace("void main() {", "void main( {"))), True)

print("\n-- finding what uses a shader varying --")
defs, uses = ga.find_references("texcoord", [str(box)])
check("defined in both files", len(defs) >= 2, True)
check("and used", len(uses) >= 1, True)
check("both shader files appear",
      len({Path(spot.split(":")[0]).name for spot in defs + uses}), 2)
defs, uses = ga.find_references("nosuchname", [str(box)])
check("a name that isn't there finds nothing", (defs, uses), ([], []))

print("\n-- the case that motivated this: renaming one half of a shader pair --")
renamed = VERT.replace("texcoord", "uv")
warning = ga.broken_references(shaders / "gbuffers_basic.vsh", VERT, renamed, [str(box)])
check("it warns", bool(warning), True)
check("naming the symbol that went", "'texcoord'" in warning, True)
check("and where it is still used", ".fsh" in warning, True)
check("and says what to do", "update those files" in warning, True)

print("\n-- and does not cry wolf --")
check("an unrelated edit is silent",
      ga.broken_references(shaders / "gbuffers_basic.vsh", VERT,
                           VERT.replace("gl_Color", "vec4(1.0)"), [str(box)]), "")
solo = box / "solo.py"
solo.write_text("def only_i_use_this():\n    return 1\n\n\ndef keep():\n    return 2\n")
check("removing something nothing else uses is silent",
      ga.broken_references(solo, solo.read_text(),
                           "def keep():\n    return 2\n", [str(box)]), "")
check("a brand new file has no baseline to compare",
      ga.broken_references(shaders / "new.frag", "", FRAG, [str(box)]), "")

print("\n-- comments and strings are not references --")
py = box / "mod.py"
py.write_text('WIDGET = 1\n\n\ndef go():\n    return WIDGET\n')
other = box / "uses.py"
other.write_text('# WIDGET is mentioned here\nLABEL = "WIDGET in a string"\n')
defs, uses = ga.find_references("WIDGET", [str(box)])
check("the real definition is found", any("mod.py" in d for d in defs), True)
check("the comment and the string are not counted",
      any("uses.py" in spot for spot in defs + uses), False)

print("\n-- the USAGES tool --")
out = ga.describe_references("texcoord")
check("it reports where it is defined", "defined at:" in out, True)
check("and warns about changing it", "breaks every one" in out, True)
out = ga.describe_references("")
check("an empty name is refused", "needs a name" in out, True)
out = ga.describe_references("foo bar(")
check("free text is sent to FIND instead", "FIND" in out, True)
out = ga.describe_references("nosuchsymbol")
check("an absent name says so plainly", "does not appear" in out, True)

print("\n-- writes and edits carry the warnings --")
target = shaders / "gbuffers_basic.vsh"
out = ga.do_write(str(target), renamed)
check("WRITE warns about the broken pair", "still used at" in out, True)
target.write_text(VERT)
out = ga.do_edit(str(target), "    tint = gl_Color;\n|||\n    tint = gl_Color\n")
check("EDIT catches the syntax it just broke", "introduced a" in out, True)
caller = box / "caller.py"
caller.write_text("from mod import go\n\n\ndef main():\n    return go()\n")
lib = box / "mod.py"
lib.write_text("WIDGET = 1\n\n\ndef go():\n    return WIDGET\n")
out = ga.do_edit(str(lib), "def go():\n|||\ndef run():\n")
check("EDIT catches the reference it just broke", "still used at" in out, True)
check("naming the function that vanished", "'go'" in out, True)
check("a two-character name is not too short to track - 'uv' is every shader's varying",
      "uv" in ga.defined_symbols(shaders / "v.vert",
                                 "varying vec2 uv;\nvoid main() { uv = vec2(0.0); }\n"), True)
target.write_text(VERT)
out = ga.do_write(str(target), VERT.replace("ftransform()", "ftransform( )"))
check("a harmless rewrite stays quiet", "WARNING" in out, False)

print("\n-- fast enough to run on every write --")
for i in range(120):
    (box / f"filler{i}.py").write_text(f"def helper{i}():\n    return {i}\n" * 12)
start = time.time()
ga.do_write(str(shaders / "gbuffers_basic.vsh"), renamed)
elapsed = time.time() - start
print(f"     {elapsed * 1000:.0f} ms across {len(ga.code_files([str(box)]))} files")
check("a write with a broken reference stays under a second", elapsed < 1.0, True)
start = time.time()
ga.do_write(str(box / "plain.py"), "def a():\n    return 1\n")
print(f"     {(time.time() - start) * 1000:.0f} ms when nothing was removed")
check("and a clean write is quicker still", time.time() - start < 1.0, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
