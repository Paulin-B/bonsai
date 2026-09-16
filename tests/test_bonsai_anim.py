import sys
from bonsai_under_test import APP, load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

print("\n-- the tree grows from the roots up --")
cells, width, height = ga.bonsai_cells(ga.BONSAI_SHAPES[0])
check("every shape is seven rows or fewer",
      max(len(s.strip("\n").split("\n")) for s in ga.BONSAI_SHAPES) <= 7, True)
check("cells are ordered bottom-first", cells[0][0], height - 1)
check("the crown comes last", cells[-1][0] < height - 1, True)
check("no cell is placed twice", len({(r, c) for r, c, _ in cells}), len(cells))

def rendered(upto):
    grid = [[" "] * width for _ in range(height)]
    for r, c, ch in cells[:upto]:
        grid[r][c] = ch
    return [ "".join(row).rstrip() for row in grid ]

early = rendered(len(cells) // 5)
check("early growth is only near the base",
      all(not line for line in early[:height // 2]), True)
check("the base is already drawn", bool(early[-1]), True)
full = rendered(len(cells))
check("fully grown fills the top too", bool(full[0]), True)

print("\n-- the widget --")
app = ga.QApplication(sys.argv)
tree = ga.BonsaiGrowth()
check("starts fully grown, so idle is not an empty box", tree.shown, len(tree.cells))
tree.start()
check("growing resets to nothing", tree.shown, 0)
check("timer running", tree.timer.isActive(), True)
for _ in range(5): tree.advance()
check("advances", tree.shown, 5)
tree.stop()
check("stopping completes the tree", tree.shown, len(tree.cells))
check("timer stopped", tree.timer.isActive(), False)

print("\n-- it loops rather than freezing while still busy --")
tree.start()
for _ in range(len(tree.cells) + 1): tree.advance()
check("a finished tree is replaced by a sapling", tree.shown <= 1, True)

print("\n-- font size is declared in both places, or it is measured wrong --")
text = APP.read_text()
check("stylesheet sets it", "font-size: 9px" in text, True)
check("widget uses the same constant", "font.setPixelSize(BONSAI_FONT_PX)" in text, True)
from PyQt6.QtGui import QFontMetrics
check("the label is tall enough for the tallest shape",
      tree.height() >= QFontMetrics(tree.font()).lineSpacing() * 7, True)

print("\n-- status and context moved below the composer --")
check("not in the top bar any more",
      text.index("self.status = QLabel(") > text.index("wrap_layout.addWidget(composer)"), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
