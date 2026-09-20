import sys, importlib.util
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

d = Path(__file__).parent / "readcases"; d.mkdir(exist_ok=True)
small = d / "small.py"; small.write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
big = d / "big.py"; big.write_text("".join(f"line {i}\n" for i in range(1, 3001)))

print("\n-- short file: numbered, whole, no truncation noise --")
out = ga.read_file(str(small))
print("\n".join("      " + l for l in out.splitlines()))
check("header names the range", out.splitlines()[0].endswith("(lines 1-5 of 5):"), True)
check("line 2 numbered and indented", "2|     return 1" in out, True)
check("no 'more lines' footer", "more lines" in out, False)

print("\n-- long file: window, and the footer tells it how to continue --")
out = ga.read_file(str(big))
check("stops at the window", "  1| line 1" in out and "400| line 400" in out, True)
check("does not overshoot", "401| line 401" in out, False)
check("footer names remaining lines", "...[2600 more lines." in out, True)
check("footer gives a usable next call", f"READ_FILE: {big} | 401-800" in out, True)

print("\n-- explicit ranges --")
out = ga.read_file(f"{big} | 1500-1503")
check("range header", out.splitlines()[0].endswith("(lines 1500-1503 of 3000):"), True)
check("range content", [l.split("| ", 1)[1] for l in out.splitlines()[1:5]],
      ["line 1500", "line 1501", "line 1502", "line 1503"])
check("open-ended range windows", ga.read_file(f"{big} | 2900-").splitlines()[0].endswith("(lines 2900-3000 of 3000):"), True)
check("past the end is explained", ga.read_file(f"{big} | 9000-9100").startswith("[big.py has only 3000 lines"), True)
check("bad range is explained", ga.read_file(f"{big} | abc").startswith("[Couldn't read 'abc' as a line range"), True)
check("range clamped to file end", ga.read_file(f"{small} | 3-99").splitlines()[0].endswith("(lines 3-5 of 5):"), True)

print("\n-- native call carries the range through --")
check("with range", ga.native_call_to_tool("read_file", {"path": "/t/a.py", "start_line": 10, "end_line": 40}),
      ("READ_FILE", "/t/a.py | 10-40"))
check("start only", ga.native_call_to_tool("read_file", {"path": "/t/a.py", "start_line": 10}),
      ("READ_FILE", "/t/a.py | 10-"))
check("no range unchanged", ga.native_call_to_tool("read_file", {"path": "/t/a.py"}),
      ("READ_FILE", "/t/a.py"))

print("\n-- attachments stay raw (line numbers would land in the written file) --")
check("attachment content verbatim", ga.read_file(str(small), numbered=False), small.read_text())

print("\n-- guards and edge cases still hold --")
check("missing file", ga.read_file(str(d / "nope.py")).startswith("[File not found"), True)
check("directory refused", ga.read_file(str(d)).startswith("[Not a regular file"), True)
check("sensitive refused", ga.read_file("~/.ssh/id_rsa").startswith("[Refused:"), True)
empty = d / "empty.py"; empty.write_text("")
check("empty file", ga.read_file(str(empty)), f"(empty file: {empty})")

print("\n-- EDIT catches a needle that kept its line numbers --")
f = d / "target.py"; f.write_text("def go():\n    return 1\n")
r = ga.do_edit(str(f), "  2|     return 1\n|||\n    return 2")
check("names the real mistake", "line numbers attached" in r, True)
check("changed nothing", f.read_text(), "def go():\n    return 1\n")
r = ga.do_edit(str(f), "    return 1\n|||\n    return 2")
check("clean copy still works", f.read_text(), "def go():\n    return 2\n")

print("\n-- a binary file is described, not decoded into nonsense --")
# Asked to animate an attached .aseprite, a run replied that the file was "corrupted
# or incomplete". It was not: attaching read it as text with errors="replace", so what
# reached the model was pages of replacement characters, and the only honest reading of
# that IS a damaged file. Bonsai handed over the nonsense.
png = d / "sheet.png"
png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02" * 400)
out = ga.read_file(str(png), numbered=False)
check("a PNG is named as one", "PNG image" in out, True)
check("with its size", "KB" in out or "bytes" in out, True)
check("and it says this is not damage", "NOT a damaged file" in out, True)
check("it says where the file is", str(png) in out, True)
check("and how to look at an image", "LOOK at it" in out, True)
check("none of the bytes are shown", "�" in out, False)

ase = d / "bot.aseprite"
ase.write_bytes(b"\x2e\x21\xe0\x42" + bytes(range(256)) * 8)
out = ga.read_file(str(ase), numbered=False)
check("an .aseprite is recognised by its extension", "Aseprite sprite" in out, True)
check("with the right article", "is an Aseprite" in out, True)

blob = d / "thing.bin"
blob.write_bytes(bytes(range(256)) * 20)
check("something unrecognised still says it is binary",
      "binary file" in ga.read_file(str(blob), numbered=False), True)

print("\n-- and text is still read as text --")
script = d / "ok.gd"
script.write_text("extends Node\n\nfunc go():\n\treturn 1\n")
out = ga.read_file(str(script), numbered=False)
check("a .gd file comes back as its contents", out.startswith("extends Node"), True)
check("not as a description", out.startswith("["), False)
utf8 = d / "accents.md"
utf8.write_text("# Café\n\nRésumé of the naïve approach - 日本語 too.\n")
out = ga.read_file(str(utf8), numbered=False)
check("non-ASCII text is not mistaken for binary", "Café" in out, True)
check("including CJK", "日本語" in out, True)

print("\n-- and the numbered form describes it too --")
check("READ_FILE on a binary says what it is",
      "PNG image" in ga.read_file(str(png)), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
