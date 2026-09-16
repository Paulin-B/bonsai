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

app = ga.QApplication(sys.argv)
box = Path(tempfile.mkdtemp(prefix="bonsai-pdf-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","SCREEN_LOG_FILE",
          "TASKS_FILE","SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)
work = box / "docs"; work.mkdir()

from PyQt6.QtGui import QImage
shot = QImage(320, 180, QImage.Format.Format_RGB32); shot.fill(0x2f6fed)
shot.save(str(work / "waybar.png"))

print("\n-- a document with a heading, a table and a picture --")
body = ("# Hyprland setups\n\n## Minimal waybar\n\n![A bar](waybar.png)\n\n"
        "Clean and quiet.\n\n| Component | Note |\n|---|---|\n| waybar | top bar |\n"
        "| hyprpaper | wallpaper |\n")
out = ga.make_pdf(f"{work}/hyprland.pdf | {body}")
pdf = work / "hyprland.pdf"
check("written", pdf.is_file(), True)
check("reports the size", "Wrote" in out and "bytes" in out, True)
check("is really a pdf", pdf.read_bytes()[:4], b"%PDF")
raw = pdf.read_bytes()
check("the image is embedded", b"Image" in raw, True)
check("big enough to hold it", len(raw) > 8000, True)

print("\n-- a missing picture is reported, not silently blank --")
out = ga.make_pdf(f"{work}/broken.pdf | # Title\n\n![missing](nope.png)\n")
check("still writes the pdf", (work / "broken.pdf").is_file(), True)
check("but warns which image was absent", "nope.png" in out and "WARNING" in out, True)
check("  ...and says what to do", "DOWNLOAD them" in out, True)

print("\n-- a remote image is not treated as missing, just not fetched --")
out = ga.make_pdf(f"{work}/remote.pdf | ![x](https://example.org/a.png)\n")
check("no false warning for a URL", "WARNING" in out, False)

print("\n-- guards --")
check("needs a path", ga.make_pdf(" | body").startswith("[MAKE_PDF needs an output path"), True)
check("needs content", ga.make_pdf(f"{work}/x.pdf |").startswith("[MAKE_PDF needs markdown"), True)
check("protected paths refused", ga.make_pdf("/etc | # x").startswith("[Refused:"), True)
check("missing folder refused",
      ga.make_pdf(f"{box}/nope/x.pdf | # x").startswith("[Refused: folder missing"), True)
check("sensitive paths refused", ga.make_pdf("~/.ssh/x.pdf | # x").startswith("[Refused:"), True)
out = ga.make_pdf(f"{work}/noext | # Title\n\ntext\n")
check("a missing extension is added", (work / "noext.pdf").is_file(), True)

print("\n-- overwriting keeps a backup --")
before = (work / "hyprland.pdf").stat().st_size
out = ga.make_pdf(f"{work}/hyprland.pdf | # Shorter\n")
check("backed up first", "backed up to" in out, True)
check("and rewritten", (work / "hyprland.pdf").stat().st_size != before, True)

print("\n-- image search filters out what is useless in a document --")
results = [
    {"img_src": "https://x.test/nice.png", "title": "Hyprland rice", "resolution": "2560 x 1440"},
    {"img_src": "https://x.test/icon.svg", "title": "icon", "img_format": "SVG"},
    {"img_src": "https://x.test/tiny.png", "title": "thumb", "resolution": "64 x 64"},
    {"img_src": "data:image/png;base64,AAAA", "title": "inline"},
]
class Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"results": results}
real_get = ga.requests.get
ga.requests.get = lambda *a, **k: Resp()
out = ga.search_images("hyprland")
ga.requests.get = real_get
check("keeps the usable one", "nice.png" in out, True)
check("drops SVG icons", "icon.svg" in out, False)
check("drops tiny thumbnails", "tiny.png" in out, False)
check("drops data URIs", "data:image" in out, False)
check("tells it to download first", "DOWNLOAD one to a file" in out, True)

print("\n-- the turn record no longer leaks into a reopened chat --")
stored = ("[SYSTEM RECORD - what the app actually did this turn:\n- ran SEARCH and got: x\n"
          "Anything not listed as succeeding here did NOT happen.]\n\nHere is the answer.")
check("stripped", ga.ACTIONS_ECHO_RE.sub("", stored).strip(), "Here is the answer.")
check("switch_chat strips it",
      "ACTIONS_ECHO_RE.sub(\"\", message[\"content\"])" in APP.read_text(), True)

print("\n-- oversized images are scaled to the page, not left to run off it --")
huge = QImage(2560, 1440, QImage.Format.Format_RGB32); huge.fill(0x333333)
huge.save(str(work / "huge.png"))
doc = ga.QTextDocument()
from PyQt6.QtCore import QUrl
doc.setBaseUrl(QUrl.fromLocalFile(f"{work}/"))
doc.setMarkdown("![big](huge.png)\n")
ga.fit_images(doc, 500.0, 350.0)

def first_image_size(document):
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.isImageFormat():
                image = fmt.toImageFormat()
                return image.width(), image.height()
            it += 1
        block = block.next()
    return None

size = first_image_size(doc)
check("an image was found", size is not None, True)
check("width fits the page", size[0] <= 500.0, True)
check("height fits the limit", size[1] <= 350.0, True)
check("proportions kept", round(size[0] / size[1], 2), round(2560 / 1440, 2))

small = QImage(120, 80, QImage.Format.Format_RGB32); small.fill(0x444444)
small.save(str(work / "small.png"))
doc2 = ga.QTextDocument()
doc2.setBaseUrl(QUrl.fromLocalFile(f"{work}/"))
doc2.setMarkdown("![small](small.png)\n")
ga.fit_images(doc2, 500.0, 350.0)
check("a small image is not blown up", first_image_size(doc2), (120.0, 80.0))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
