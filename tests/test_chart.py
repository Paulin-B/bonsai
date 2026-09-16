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
box = Path(tempfile.mkdtemp(prefix="bonsai-chart-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","SCREEN_LOG_FILE",
          "TASKS_FILE","SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)
work = box / "charts"; work.mkdir()

print("\n-- reading label=value pairs the way a person writes them --")
points, bad = ga.parse_chart_data("vsync=16.7ms, uncapped = 4.2ms\ncapped120: 8.3ms")
check("all three parsed", [p[0] for p in points], ["vsync", "uncapped", "capped120"])
check("units stripped for the maths", [p[1] for p in points], [16.7, 4.2, 8.3])
check("units kept for the label", [p[2] for p in points], ["16.7ms", "4.2ms", "8.3ms"])
check("nothing rejected", bad, [])

points, bad = ga.parse_chart_data("- price: $1,250\n- rival: $980")
check("thousands separators and currency", [p[1] for p in points], [1250.0, 980.0])
check("list bullets are not part of the label", [p[0] for p in points], ["price", "rival"])

points, bad = ga.parse_chart_data("alpha=5, this is prose, beta=7")
check("prose is rejected, not guessed at", bad, ["this is prose"])
check("the real pairs still survive", len(points), 2)

points, _ = ga.parse_chart_data("drift=-3.5, gain=2")
check("negative values are read as negative", [p[1] for p in points], [-3.5, 2.0])

print("\n-- gridline steps land on round numbers --")
check("a span of 17 steps by 5", ga.nice_step(17, 4), 5)
check("a span of 0.9 steps by 0.2", round(ga.nice_step(0.9, 5), 4), 0.2)
check("a zero span does not divide by zero", ga.nice_step(0, 6), 1.0)
check("whole numbers print without a decimal point", ga.chart_number(12.0), "12")
check("fractions keep only what they need", ga.chart_number(16.70), "16.7")

print("\n-- arguments are recognised by shape, not by position --")
target, kind, title, data = ga.split_chart_request("/tmp/a.png | bar | Frame times | v=16.7")
check("documented order", (kind, title, data), ("bar", "Frame times", "v=16.7"))
target, kind, title, data = ga.split_chart_request("/tmp/a.png | Frame times | v=16.7")
check("a dropped type defaults to bar", kind, "bar")
check("the title is still found", title, "Frame times")
target, kind, title, data = ga.split_chart_request("/tmp/a.png | pie | a=1, b=2 | Shares")
check("title and data swapped round", (kind, title, data), ("pie", "Shares", "a=1, b=2"))

print("\n-- drawing each kind of chart --")
for kind in ("bar", "line", "pie"):
    out = ga.make_chart(f"{work}/{kind}.png | {kind} | Frame times | "
                        "vsync=16.7ms, uncapped=4.2ms, capped120=8.3ms")
    made = work / f"{kind}.png"
    check(f"{kind}: a real PNG lands on disk", made.exists() and made.stat().st_size > 2000, True)
    check(f"{kind}: the reply names the file", made.name in out, True)
    check(f"{kind}: the reply quotes the data back", "vsync 16.7ms" in out, True)
    check(f"{kind}: says how to put it in a document", f"![]({made.name})" in out, True)

image = ga.QImage(str(work / "bar.png"))
check("drawn at the documented size", (image.width(), image.height()),
      (ga.CHART_WIDTH, ga.CHART_HEIGHT))
check("something was actually painted, not a blank page",
      len({image.pixel(x, y) for x in range(0, image.width(), 7)
           for y in range(0, image.height(), 7)}) > 3, True)

print("\n-- negative values belong on a bar chart, not a pie --")
out = ga.make_chart(f"{work}/neg.png | bar | Drift | morning=-3, noon=2")
check("a bar chart takes them", (work / "neg.png").exists(), True)
out = ga.make_chart(f"{work}/pie2.png | pie | Drift | morning=-3, noon=2")
check("a pie chart refuses them", "negative" in out.lower(), True)
check("and writes nothing", (work / "pie2.png").exists(), False)

print("\n-- refusals say what to do instead --")
out = ga.make_chart(f"{work}/empty.png | bar | Nothing | just some words")
check("no data at all is refused", out.startswith("[MAKE_CHART found no data"), True)
check("and the refusal shows the format", "label=value" in out, True)
out = ga.make_chart("")
check("a missing path is refused", "needs an output path" in out, True)
out = ga.make_chart(f"{work}/many.png | bar | Too much | "
                    + ", ".join(f"n{i}={i}" for i in range(40)))
check("too many values to read is refused", str(ga.CHART_MAX_POINTS) in out, True)
out = ga.make_chart(f"{box}/nope/x.png | bar | T | a=1")
check("a missing folder says to MKDIR", "MKDIR" in out, True)

print("\n-- odd extensions and second runs --")
ga.make_chart(f"{work}/named.svg | bar | T | a=1")
check("an unsupported extension becomes .png", (work / "named.png").exists(), True)
out = ga.make_chart(f"{work}/bar.png | bar | Again | a=1")
check("overwriting keeps a backup of the old chart", "backed up" in out, True)

print("\n-- partly usable data charts what it can and says what it dropped --")
out = ga.make_chart(f"{work}/partial.png | bar | Mixed | a=1, nonsense here, b=2")
check("it still draws", (work / "partial.png").exists(), True)
check("and names what it ignored", "nonsense here" in out, True)

print("\n-- asked for a chart 'in a folder', it goes in the folder --")
inside = box / "reports"; inside.mkdir()
out = ga.make_chart(f"{inside} | bar | Frame times | vsync=16.7ms, uncapped=4.2ms")
check("named after the title, inside the folder", (inside / "frame-times.png").exists(), True)
check("not written as a sibling of it", (box / "reports.png").exists(), False)
check("and the reply names the real path", str(inside / "frame-times.png") in out, True)
out = ga.make_chart(f"{inside} | pie | | a=1, b=2")
check("an untitled chart still gets a name", (inside / "chart.png").exists(), True)
out = ga.make_pdf(f"{inside} | # Quarterly notes\n\nBody text.\n")
check("a PDF asked for in a folder lands in it too",
      (inside / "quarterly-notes.pdf").exists(), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
