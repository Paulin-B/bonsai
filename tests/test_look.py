import base64
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from bonsai_under_test import APP, load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)
box = Path(tempfile.mkdtemp(prefix="bonsai-look-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","SCREEN_LOG_FILE",
          "TASKS_FILE","SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)
work = box / "work"; work.mkdir()
ga.save_trusted([str(work)])

def decoded(payload):
    from PIL import Image
    return Image.open(BytesIO(base64.b64decode(payload)))

print("\n-- an image comes back as an image --")
ga.make_chart(f"{work}/frames.png | bar | Frame times | vsync=16.7ms, uncapped=4.2ms")
note, frame = ga.look_at(f"{work}/frames.png")
check("a picture came back", frame is not None, True)
check("the note says what it is", "frames.png" in note, True)
check("and its real dimensions", f"{ga.CHART_WIDTH}x{ga.CHART_HEIGHT}" in note, True)
image = decoded(frame)
check("it decodes as a real JPEG", image.format, "JPEG")
check("keeping the chart's proportions",
      round(image.width / image.height, 2), round(ga.CHART_WIDTH / ga.CHART_HEIGHT, 2))
check("and it is not a blank rectangle",
      len(image.convert("RGB").getcolors(maxcolors=100000) or [1]) > 3, True)

print("\n-- a PDF comes back a page at a time --")
ga.make_pdf(f"{work}/report.pdf | # Page one\n\nText.\n\n"
            + "\n\n".join(f"Filler paragraph {i}." for i in range(80))
            + "\n\n# Page two\n\nMore text.\n")
note, frame = ga.look_at(f"{work}/report.pdf")
check("a page was rendered", frame is not None, True)
check("the note says which page", "page 1" in note, True)
check("and how many there are", "of " in note, True)
check("and how to see another", "page number" in note, True)
note2, frame2 = ga.look_at(f"{work}/report.pdf | 2")
check("asking for page 2 gets page 2", "page 2" in note2, True)
check("which is a different picture", frame2 != frame, True)
pages, count = ga.render_pdf_pages(work / "report.pdf", 3, 900)
shades = ga.qimage_to_pil(pages[0]).convert("L").getextrema()
check("the page is ink on white paper, not a black rectangle", shades, (0, 255))
note3, _ = ga.look_at(f"{work}/report.pdf | 99")
check("a page past the end clamps rather than failing", "page" in note3.lower(), True)

print("\n-- documents are laid out, not dumped as text --")
(work / "notes.md").write_text("# Title\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
note, frame = ga.look_at(f"{work}/notes.md")
check("markdown renders", frame is not None, True)
check("described as laid out", "laid out" in note, True)
(work / "page.html").write_text("<h1>Hi</h1><script>alert(1)</script>")
note, frame = ga.look_at(f"{work}/page.html")
check("html renders", frame is not None, True)
check("with the honest caveat about scripts", "Scripts" in note, True)
(work / "main.py").write_text("def main():\n    return 42\n")
note, frame = ga.look_at(f"{work}/main.py")
check("so does plain source", frame is not None, True)

print("\n-- it says what went wrong instead of returning a blank --")
note, frame = ga.look_at("")
check("nothing asked for", "needs something to look at" in note, True)
check("and no picture", frame, None)
note, frame = ga.look_at(f"{work}/missing.png")
check("a file that isn't there", "does not exist" in note, True)
note, frame = ga.look_at(str(work))
check("a folder is refused", "folder" in note, True)
check("pointing at LIST_DIR", "LIST_DIR" in note, True)
(work / "broken.png").write_bytes(b"not actually a png")
note, frame = ga.look_at(f"{work}/broken.png")
check("a corrupt image is reported, not crashed on", note.startswith("[Could not render"), True)
check("with no picture", frame, None)

print("\n-- screen and window are recognised without a path --")
check("'screen' is an alias", ga.LOOK_ALIASES.get("screen"), "screen")
check("'window' is an alias", ga.LOOK_ALIASES.get("window"), "window")
check("'game' looks at the window", ga.LOOK_ALIASES.get("game"), "window")
saved = ga.capture_screen
calls = []
ga.capture_screen = lambda mode=None: (calls.append(mode), "ZmFrZQ==")[1]
try:
    note, frame = ga.look_at("screen")
    check("screen grabs the default way", calls[-1], None)
    check("and returns the frame", frame, "ZmFrZQ==")
    note, frame = ga.look_at("window")
    check("window asks for the active window", calls[-1], "active-window")
    check("and says to check what it drew", "actually drew" in note, True)
    ga.capture_screen = lambda mode=None: (_ for _ in ()).throw(RuntimeError("no grim"))
    note, frame = ga.look_at("screen")
    check("a capture failure is reported", "Could not capture" in note, True)
    check("without a picture", frame, None)
finally:
    ga.capture_screen = saved

print("\n-- what LOOK renders reaches the next step's eyes --")
cfg = dict(ga.settings()); cfg["chat_id"] = "look"
worker = ga.Worker("x", False, [], cfg)
check("nothing waiting to begin with", worker.fresh_frame, None)
seen = []
worker.preview.connect(seen.append)
result = worker.run_tool("LOOK", f"{work}/frames.png")
check("the tool returns the note", "frames.png" in result, True)
check("and leaves the picture for the loop", worker.fresh_frame is not None, True)
check("and shows the user the same file", seen, [str(work / "frames.png")])
worker.fresh_frame = None
seen.clear()
ga.capture_screen = lambda mode=None: "ZmFrZQ=="
try:
    worker.run_tool("LOOK", "screen")
    check("a screen grab still feeds the eyes", worker.fresh_frame, "ZmFrZQ==")
    check("but is not sent to the preview panel", seen, [])
finally:
    ga.capture_screen = saved

print("\n-- PREVIEW shows the user, it does not show the model --")
worker.fresh_frame = None
seen.clear()
result = worker.run_tool("PREVIEW", f"{work}/frames.png")
check("the panel is told", seen, [str(work / "frames.png")])
check("the model is told it cannot see it", "does not show it to YOU" in result, True)
check("and pointed at LOOK", "LOOK" in result, True)
check("no picture was queued", worker.fresh_frame, None)
result = worker.run_tool("PREVIEW", f"{work}/missing.png")
check("a missing file is refused", "does not exist" in result, True)
result = worker.run_tool("PREVIEW", "")
check("an empty path is refused", "needs a file path" in result, True)

print("\n-- the model is told what it can and cannot import --")
installed, absent = ga.python_libraries()
check("numpy was found", "numpy" in installed, True)
check("PIL was found", "PIL" in installed, True)
facts = ga.toolkit_facts()
check("the present ones are listed", "numpy" in facts, True)
if absent:
    check("the absent ones are named", absent[0] in facts, True)
    check("with the reason installing won't work", "no root" in facts, True)
check("charts are pointed at the chart tool", "MAKE_CHART" in facts, True)

print("\n-- the preview panel shows the user the same things --")
holder = ga.QWidget(); holder.resize(340, 700)
pane = ga.PreviewPane(holder)
pane.resize(320, 660)
holder.show()
for _ in range(4): app.processEvents()

pane.show_path(str(work / "frames.png"))
check("an image is described with its real size",
      f"{ga.CHART_WIDTH}x{ga.CHART_HEIGHT}" in pane.note.text(), True)
def image_widths(document):
    found, block = [], document.begin()
    while block.isValid():
        piece = block.begin()
        while not piece.atEnd():
            fmt = piece.fragment().charFormat()
            if fmt.isImageFormat():
                found.append(fmt.toImageFormat().width())
            piece += 1
        block = block.next()
    return found
widths = image_widths(pane.view.document())
check("and scaled down to fit the panel rather than overflowing it",
      bool(widths) and 0 < widths[0] <= pane.view.viewport().width(), True)
check("no browser button for an image", pane.browser.isVisible(), False)

pane.show_path(str(work / "report.pdf"))
check("a PDF says how many pages", "3 page PDF" in pane.note.text(), True)
check("every page is laid in", len(image_widths(pane.view.document())), 3)
check("and pages fit even once a scrollbar appears beside them",
      max(image_widths(pane.view.document())) <= pane.view.viewport().width(), True)
check("with a way out to a real viewer", pane.browser.isVisible(), True)

pane.show_path(str(work / "notes.md"))
check("markdown is named as such", "markdown" in pane.note.text(), True)
check("and rendered, not shown as source",
      "|---|" in pane.view.toPlainText(), False)

pane.show_path(str(work / "page.html"))
check("html warns that scripts do not run", "browser" in pane.note.text(), True)
check("and offers to open one", pane.browser.isVisible(), True)

pane.show_path(str(work / "main.py"))
check("source is shown as source", "def main" in pane.view.toPlainText(), True)
check("with its length", "characters" in pane.note.text(), True)

pane.show_path(str(work / "missing.png"))
check("a missing file says so", "does not exist" in pane.note.text(), True)
check("and nothing stale is left on screen", pane.view.toPlainText(), "")
check("and the path is forgotten", pane.path, None)
pane.show_path(str(work))
check("a folder is refused", "folder" in pane.note.text(), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
