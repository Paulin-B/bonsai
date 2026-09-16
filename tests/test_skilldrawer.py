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
box = Path(tempfile.mkdtemp(prefix="bonsai-drawer-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","SCREEN_LOG_FILE",
          "TASKS_FILE","SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings({**ga.DEFAULTS, "skill_dirs": []})

SKILLS = {
    "save-to-obsidian": {"name": "save-to-obsidian",
                         "description": "Write a note into the user's vault"},
    "to-spec": {"name": "to-spec", "description": "Turn a rough idea into a spec"},
    "screenshot-review": {"name": "screenshot-review",
                          "description": "Critique a screenshot of a user interface"},
}

print("\n-- where the slash counts as opening a skill name --")
check("at the very start", ga.skill_query("/sav", 4), (0, "sav"))
check("a bare slash offers everything", ga.skill_query("/", 1), (0, ""))
check("after a space, mid-sentence", ga.skill_query("use /to-s", 9), (4, "to-s"))
check("a path is not a skill name", ga.skill_query("/home/sam", 13), None)
check("nor is a slash inside a word", ga.skill_query("and/or", 6), None)
check("nothing once the name is finished with a space", ga.skill_query("/to-spec ", 9), None)
check("only what is behind the cursor matters",
      ga.skill_query("/to-spec this file", 4), (0, "to-"))
check("plain text opens nothing", ga.skill_query("hello", 5), None)

print("\n-- the closest name comes first, not merely a matching one --")
names = [n for n, _ in ga.match_skills("s", SKILLS)]
check("a name that starts with it outranks one that contains it",
      names.index("save-to-obsidian") < names.index("to-spec"), True)
check("screenshot-review starts with it too", "screenshot-review" in names[:2], True)
check("an empty query lists them all", len(ga.match_skills("", SKILLS)), 3)
check("descriptions are searched as well",
      [n for n, _ in ga.match_skills("vault", SKILLS)], ["save-to-obsidian"])
check("but rank below names", ga.match_skills("spec", SKILLS)[0][0], "to-spec")
check("underscores and hyphens are the same thing",
      [n for n, _ in ga.match_skills("to_spec", SKILLS)], ["to-spec"])
check("a name that matches nothing matches nothing", ga.match_skills("zzz", SKILLS), [])

print("\n-- picking one completes the text --")
window = ga.QWidget()
inp = ga.InputBox()
inp.setParent(window)
inp.skills_source = lambda: SKILLS
drawer = ga.SkillDrawer(window)
drawer.chosen.connect(inp.insert_skill)
inp.drawer = drawer
inp.anchor = inp
# isVisible() is false for any child of an unshown window, so the window has to be up
# for "did the drawer open" to mean anything.
window.show()
inp.setFocus()
app.processEvents()

inp.setPlainText("/sav")
cursor = inp.textCursor(); cursor.setPosition(4); inp.setTextCursor(cursor)
check("the drawer opened", inp.drawer_open(), True)
check("showing the match", drawer.current(), "save-to-obsidian")
inp.insert_skill(drawer.current())
check("the typed fragment became the full name", inp.toPlainText(), "/save-to-obsidian ")
check("and the drawer closed", inp.drawer_open(), False)
check("the cursor is left after the trailing space", inp.textCursor().position(), 18)

inp.setPlainText("use /to-s on this")
cursor = inp.textCursor(); cursor.setPosition(9); inp.setTextCursor(cursor)
check("it opens mid-sentence", inp.drawer_open(), True)
inp.insert_skill("to-spec")
check("and only the fragment is replaced", inp.toPlainText(), "use /to-spec  on this")

print("\n-- the drawer stays out of the way the rest of the time --")
inp.setPlainText("what is my gpu")
cursor = inp.textCursor(); cursor.setPosition(14); inp.setTextCursor(cursor)
check("ordinary typing keeps it shut", inp.drawer_open(), False)
inp.setPlainText("/sav")
cursor = inp.textCursor(); cursor.setPosition(4); inp.setTextCursor(cursor)
check("open again", inp.drawer_open(), True)
inp.clear()
check("clearing the box closes it", inp.drawer_open(), False)

print("\n-- a name that matches nothing says so rather than vanishing --")
inp.setPlainText("/zzzz")
cursor = inp.textCursor(); cursor.setPosition(5); inp.setTextCursor(cursor)
check("still shown", inp.drawer_open(), True)
check("with an explanation", "No skill matches" in drawer.list.item(0).text(), True)
check("and nothing selectable", drawer.current(), None)
check("so Enter falls through to sending", inp.insert_skill(drawer.current()), False)

print("\n-- moving through the list wraps --")
inp.setPlainText("/")
cursor = inp.textCursor(); cursor.setPosition(1); inp.setTextCursor(cursor)
first = drawer.current()
drawer.move_selection(1)
check("down moves on", drawer.current() != first, True)
drawer.move_selection(-1)
check("up comes back", drawer.current(), first)
drawer.move_selection(-1)
check("and wraps past the top", drawer.current(), ga.match_skills("", SKILLS)[-1][0])

print("\n-- sending /name asks for the skill, it does not merely mention it --")
out = ga.expand_skill_shortcut("/to-spec the bonsai chart tool", SKILLS)
check("the skill is named", '"to-spec"' in out, True)
check("the tool call is named too", "USE_SKILL" in out, True)
check("the rest of the message survives", "the bonsai chart tool" in out, True)
check("a bare shortcut still works", "USE_SKILL" in ga.expand_skill_shortcut("/to-spec", SKILLS), True)
check("an unknown name is left alone",
      ga.expand_skill_shortcut("/nonsense do a thing", SKILLS), "/nonsense do a thing")
check("a path is left alone",
      ga.expand_skill_shortcut("/home/sam is mine", SKILLS), "/home/sam is mine")
check("ordinary prose is left alone",
      ga.expand_skill_shortcut("what is my gpu", SKILLS), "what is my gpu")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
