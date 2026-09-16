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

print("\n-- every palette is complete, so no rule can fall back to nothing --")
keys = set(ga.PALETTES["Midnight"])
for name, colours in ga.PALETTES.items():
    check(f"{name} has the same keys", set(colours), keys)
    check(f"  {name} says whether it is dark", isinstance(colours["dark"], bool), True)
    bad = [k for k, v in colours.items() if k != "dark" and not str(v).startswith("#")]
    check(f"  {name} is all colours", bad, [])
check("both neutrals exist", {ga.NEUTRAL_DARK, ga.NEUTRAL_LIGHT} <= set(ga.PALETTES), True)
check("the default exists", ga.DEFAULT_THEME in ga.PALETTES, True)
check("there are dark and light ones",
      {c["dark"] for c in ga.PALETTES.values()}, {True, False})

print("\n-- text stays readable on its background --")
def luminance(hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    channels = [(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
                for c in (r, g, b)]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)

for name, c in ga.PALETTES.items():
    check(f"{name}: body text on surface is legible", round(contrast(c["text"], c["surface"]), 1) >= 7.0, True)
    check(f"  {name}: muted text still readable", contrast(c["muted"], c["surface"]) >= 3.0, True)
    check(f"  {name}: text on the accent is legible", contrast(c["on_accent"], c["accent"]) >= 3.0, True)

print("\n-- a control's edge is visible without hovering it --")
# Measured before this existed: a field's own fill scores 1.03-1.15 against the panel
# behind it and the palette's divider colour 1.16-1.49, so every input looked untouched
# by the theme until the pointer was over it and the border jumped to the accent.
for name, colours in ga.PALETTES.items():
    edge = ga.control_edge(colours)
    check(f"{name}: edge reads against the panel",
          ga.contrast(edge, colours["surface"]) >= ga.CONTROL_EDGE_RATIO, True)
    check(f"  {name}: and against the sidebar",
          ga.contrast(edge, colours["bg"]) >= ga.CONTROL_EDGE_RATIO, True)
    check(f"  {name}: it is derived, not the divider colour", edge == colours["border"], False)
    check(f"  {name}: and still quieter than the body text",
          ga.contrast(edge, colours["surface"]) < ga.contrast(colours["text"], colours["surface"]),
          True)
sheet = ga.stylesheet("Sumi Ink")
check("inputs use it", f"1px solid {ga.control_edge(ga.PALETTES['Sumi Ink'])}" in sheet, True)
check("dividers keep the quiet one", ga.PALETTES["Sumi Ink"]["border"] in sheet, True)

print("\n-- the stylesheet takes a name, and still takes the old boolean --")
sheet = ga.stylesheet("Cherry Blossom")
check("the palette is used", ga.PALETTES["Cherry Blossom"]["accent"] in sheet, True)
check("True still means dark", ga.PALETTES[ga.NEUTRAL_DARK]["surface"] in ga.stylesheet(True), True)
check("False still means light", ga.PALETTES[ga.NEUTRAL_LIGHT]["surface"] in ga.stylesheet(False), True)
check("an unknown name falls back rather than raising",
      ga.palette("Nonsense"), ga.PALETTES[ga.DEFAULT_THEME])
check("so does an unknown font", ga.font_stack("Nonsense"), ga.FONT_STACKS[ga.DEFAULT_FONT])

print("\n-- fonts and size reach the stylesheet --")
check("the family is applied", "SF Pro Rounded" in ga.stylesheet("Midnight", "Rounded"), True)
check("the size is applied", "font-size: 17px" in ga.stylesheet("Midnight", "System", 17), True)
check("every font option is a real stack",
      all("," in stack for stack in ga.FONT_STACKS.values()), True)

print("\n-- asking before the app exists must not kill the process --")
check("no QApplication yet, so no answer - and no crash", ga.system_font_family(), None)
check("and the plain stack is used meanwhile", ga.font_stack("System"),
      ga.FONT_STACKS["System"])
# The bug this guards: none of the stack was installed, fontconfig substituted
# Cantarell, and Qt instances that variable font so badly that regular text looks
# semi-bold and bold comes out smeared.
for bad in ("Cantarell", "Adwaita Sans"):
    check(f"{bad} is never preferred", bad in ga.PREFERRED_UI_FONTS, False)

print("\n-- in the window --")
box = Path(tempfile.mkdtemp(prefix="bonsai-theme-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_on_open": False,
                  "briefing_enabled": False, "theme": "Bonsai Green"})
ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
win = ga.Bonsai(); win.resize(900, 600); win.show()
def settle(n=4):
    for _ in range(n): app.processEvents()
settle()

def painted():
    """The stylesheet actually in force - it lives on the application now, so that
    dropdowns and other top-level popups get it too."""
    return app.styleSheet() or win.styleSheet()

check("the chosen theme is in force", win.theme_now(), "Bonsai Green")
check("and painted", ga.PALETTES["Bonsai Green"]["accent"] in painted(), True)
check("light and dark are themes, not a separate switch",
      hasattr(win, "dark_box"), False)

win.settings["theme"] = "Cherry Blossom"
win.apply_theme()
settle()
check("switching repaints", ga.PALETTES["Cherry Blossom"]["accent"] in painted(), True)
win.settings["theme"] = "Paper"
win.apply_theme()
settle()
check("choosing a light theme is all it takes", ga.is_dark(win.theme_now()), False)
check("and it is painted", ga.PALETTES["Paper"]["bg"] in painted(), True)

print("\n-- a settings file from before themes still opens in the right mode --")
win.settings.pop("theme", None)
win.settings["dark_mode"] = False
check("an old light setup lands on the light neutral", win.theme_now(), ga.NEUTRAL_LIGHT)
win.settings["dark_mode"] = True
check("an old dark setup lands on the dark neutral", win.theme_now(), ga.NEUTRAL_DARK)

print("\n-- with the app up, the font is resolved from what is installed --")
from PyQt6.QtGui import QFontDatabase
ga.theme._installed_ui_font = "unset"
found = ga.system_font_family()
installed = set(QFontDatabase.families())
check("it picks something that genuinely exists", found is None or found in installed, True)
check("and the most preferred one available",
      found, next((f for f in ga.PREFERRED_UI_FONTS if f in installed), None))
stack = ga.font_stack("System")
check("the resolved font leads the stack",
      stack.startswith(f'"{found}"') if found else True, True)
check("the original names stay as fallbacks for other machines", "Segoe UI" in stack, True)
check("an explicit choice is honoured exactly as written",
      ga.font_stack("Mono"), ga.FONT_STACKS["Mono"])
ga.theme._installed_ui_font = None
check("a machine with none of them falls back to the plain stack",
      ga.font_stack("System"), ga.FONT_STACKS["System"])
ga.theme._installed_ui_font = "unset"

print("\n-- controls are painted in the theme, not in system grey --")
from PyQt6.QtGui import QColor

def sampled(widget, x, y):
    shot = widget.grab().toImage()
    return QColor(shot.pixel(x, y)).name()

for theme in ("Midnight", "Cherry Blossom"):
    win.settings["theme"] = theme
    win.apply_theme()
    settle()
    field = QColor(ga.PALETTES[theme]["field"]).name()
    sidebar = QColor(ga.PALETTES[theme]["bg"]).name()
    got = sampled(win.trust_picker, 6, win.trust_picker.height() // 2)
    # The bug: '#sidebar QWidget { background: transparent }' outranks the plain
    # QComboBox rule, so the folder picker showed the sidebar through it in every
    # theme while the box above it tinted correctly.
    check(f"{theme}: the folder picker is a field, not the sidebar", got, field)
    check(f"  {theme}: and not the sidebar colour", got == sidebar, False)

print("\n-- a window-scoped stylesheet does not reach a dropdown --")
# The bug, reproduced: a combo's popup is its own top-level window. Setting the sheet
# on the main window leaves the popup's rows to the system palette - white, with the
# theme's light text on them - while the one row under the cursor picked up ::item:hover
# and looked right. That is why it read as "only themed when highlighted over".
from PyQt6.QtWidgets import QComboBox, QVBoxLayout
from PyQt6.QtCore import QItemSelectionModel

def popup_backgrounds(scope):
    app.setStyleSheet("")
    holder = ga.QWidget(); rows = QVBoxLayout(holder)
    combo = QComboBox(); combo.addItems(["/media/D/Storage", "/home/sam/Vault"])
    rows.addWidget(combo)
    sheet = ga.stylesheet("Sumi Ink", "System", 13)
    (app if scope == "app" else holder).setStyleSheet(sheet)
    holder.resize(420, 60); holder.show(); settle(3)
    combo.showPopup(); settle(5)
    shot = combo.view().window().grab().toImage()
    found = {QColor(shot.pixel(4, y)).name()
             for y in range(3, shot.height() - 3, 4)}
    combo.hidePopup(); holder.close()
    return found

check("window-scoped leaves system white in the popup",
      "#ffffff" in popup_backgrounds("window"), True)
check("application-scoped does not", "#ffffff" in popup_backgrounds("app"), False)

src = APP.read_text()
check("so the app themes the application, not the window",
      "app.setStyleSheet(sheet)" in src, True)

print("\n-- and the popup rows are themed in every palette --")
for theme in ga.PALETTES:
    app.setStyleSheet(ga.stylesheet(theme, "System", 13))
    holder = ga.QWidget(); rows = QVBoxLayout(holder)
    combo = QComboBox(); combo.addItems(["/media/D/Storage", "/home/sam/Vault/Bonsai"])
    rows.addWidget(combo); holder.resize(420, 60); holder.show()
    settle(3)
    combo.showPopup(); settle(4)
    view = combo.view()
    view.selectionModel().select(view.model().index(1, 0),
                                 QItemSelectionModel.SelectionFlag.ClearAndSelect)
    settle(3)
    shot = view.grab().toImage()
    # Sample the row backgrounds a few pixels in from the edge, clear of any glyph:
    # antialiased text produces hundreds of intermediate shades that say nothing about
    # whether the row itself is themed.
    colours = ga.PALETTES[theme]
    known = {QColor(v).name() for k, v in colours.items() if k != "dark"}
    known.add(QColor(ga.control_edge(colours)).name())
    backgrounds = {QColor(shot.pixel(4, y)).name()
                   for y in range(4, shot.height() - 4, 3)}
    check(f"{theme}: every row background comes from the palette",
          backgrounds - known, set())
    if colours["dark"]:
        # The reported bug: the row under the cursor painted from the system palette,
        # a white bar with the theme's light text on it.
        check(f"  {theme}: and none of them is light",
              max(ga.contrast(c, "#ffffff") for c in backgrounds) > 2.0, True)
    combo.hidePopup(); holder.close()

sheet = ga.stylesheet("Sumi Ink")
for rule in ("QComboBox QAbstractItemView::item",
             "QComboBox QAbstractItemView::item:hover",
             "QComboBox QAbstractItemView::item:selected"):
    check(f"'{rule.split('::')[-1]}' is styled", rule in sheet, True)
check("and the selected row's text colour is set too", "selection-color" in sheet, True)

print("\n-- the arrows are generated per palette --")
down = ga.arrow_icon("#8b8b93", "down")
up = ga.arrow_icon("#8b8b93", "up")
check("a down chevron is produced", down is not None and Path(down).exists(), True)
check("and an up one", up is not None and Path(up).exists(), True)
check("they are different files", down != up, True)
check("a different colour is a different file",
      ga.arrow_icon("#ff0000", "down") != down, True)
check("asking twice reuses the file", ga.arrow_icon("#8b8b93", "down"), down)
before = Path(down).stat().st_mtime_ns
ga.arrow_icon("#8b8b93", "down")
check("and does not redraw it", Path(down).stat().st_mtime_ns, before)

from PyQt6.QtGui import QImage
glyph = QImage(str(down))
check("the chevron has pixels in it",
      any(QColor(glyph.pixel(x, y)).alpha() > 0
          for x in range(glyph.width()) for y in range(glyph.height())), True)
check("the stylesheet points at it", Path(down).as_posix() in ga.stylesheet("Midnight"), True)
check("spin buttons use them too", "QSpinBox::up-arrow" in ga.stylesheet("Midnight"), True)

check("a settings file naming no known theme still paints",
      (win.settings.__setitem__("theme", "Deleted Theme"), win.theme_now())[1]
      in ga.PALETTES, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
