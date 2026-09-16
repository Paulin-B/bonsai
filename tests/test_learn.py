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

box = Path(tempfile.mkdtemp(prefix="bonsai-learn-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.save_settings({**ga.DEFAULTS, "skill_dirs": []})

CHART_TURN = ("MAKE_CHART: /tmp/a.png | bar -> Wrote 14000 bytes\n"
              "LOOK: /tmp/a.png -> Here is 'a.png'\n")

print("\n-- the shape of a turn --")
check("tools in order", ga.turn_signature(CHART_TURN), "make_chart>look")
check("file_op keeps its action",
      ga.turn_signature("FILE_OP: WRITE | /a.py | x -> Wrote 20\nRUN: pytest -> exit 0"),
      "file_op:write>run")
check("write and edit are different procedures",
      ga.turn_signature("FILE_OP: EDIT | /a.py -> Edited") !=
      ga.turn_signature("FILE_OP: WRITE | /a.py -> Wrote"), True)
check("consecutive repeats collapse - three files is the same shape as five",
      ga.turn_signature("FILE_OP: WRITE | a -> ok\nFILE_OP: WRITE | b -> ok\n"
                        "FILE_OP: WRITE | c -> ok\nRUN: pytest -> exit 0"),
      "file_op:write>run")
check("refusals are not part of the procedure",
      ga.turn_signature("FILE_OP: EDIT | a -> [No match in a.py]\nREAD_FILE: a -> ok"),
      "read_file")
check("an empty trace has no shape", ga.turn_signature(""), "")

print("\n-- one call is an action, not a procedure --")
sig, count = ga.record_pattern("READ_FILE: /a.py -> ok", "read that file")
check("a single step is not recorded", count, 0)
check("and nothing is stored", ga.load_patterns()["patterns"], [])

print("\n-- repetition is what makes it a procedure --")
for i in range(1, 4):
    sig, count = ga.record_pattern(CHART_TURN, f"make me a chart of thing {i}")
    check(f"seen {i} time(s)", count, i)
    ready = ga.pattern_ready(sig)
    check(f"  ready to learn after {i}?", ready is not None, i >= ga.PATTERN_REPEATS)
check("the requests are kept as examples",
      len(ga.load_patterns()["patterns"][0]["requests"]), 3)

print("\n-- a different shape is tracked separately --")
sig2, count2 = ga.record_pattern("FILE_OP: WRITE | a -> ok\nRUN: pytest -> exit 0", "write tests")
check("counted on its own", count2, 1)
check("two patterns now", len(ga.load_patterns()["patterns"]), 2)

print("\n-- what the model drafts has to survive the gate --")
sig = "make_chart>look"
for bad, why in [
    ("just some prose with no pipes", "not 'name | description | instructions'"),
    ("!! | does things | " + "x" * 100, "not a usable skill name"),
    ("x | does things | " + "x" * 100, "not a usable skill name"),
    ("draw-chart | | " + "x" * 100, "no description"),
    ("draw-chart | makes a chart | too short", "too thin"),
]:
    name, reason = ga.accept_learned_skill(bad, sig)
    check(f"rejected: {why}", (name, why in reason), (None, True))
check("nothing was saved", ga.load_skills()["skills"], [])

# A title-case name is the model being sloppy, not a reason to lose the skill.
name, reason = ga.accept_learned_skill(
    "Draw Chart | Makes a chart from numbers | " + "step. " * 30, sig)
check("a sloppy name is normalised rather than refused", name, "draw-chart")
ga.save_json(ga.SKILLS_FILE, {"skills": []})
ga.save_json(ga.PATTERNS_FILE, {"patterns": [
    {"signature": sig, "count": 3, "requests": ["a", "b"], "skill": None}]})

good = ("draw-comparison-chart | Turn a set of labelled numbers into a chart and check "
        "it | Call make_chart with label=value pairs, keeping the units on the values. "
        "Then call look on the file it wrote and confirm the labels are readable before "
        "telling the user it is done.")
name, reason = ga.accept_learned_skill(good, sig)
check("a good draft is accepted", name, "draw-comparison-chart")
check("and saved", len(ga.load_skills()["skills"]), 1)
check("marked as learned rather than hand-written",
      ga.load_skills()["skills"][0]["learned"], True)
check("it remembers which shape produced it",
      ga.load_skills()["skills"][0]["signature"], sig)

print("\n-- learned once, not every time from then on --")
check("the pattern is stamped",
      ga.load_patterns()["patterns"][0]["skill"], "draw-comparison-chart")
ga.record_pattern(CHART_TURN, "another chart please")
check("doing it a fourth time does not learn it again", ga.pattern_ready(sig), None)
name, reason = ga.accept_learned_skill(good, sig)
check("nor can the same name be saved twice", name, None)
check("saying why", "already exists" in reason, True)

print("\n-- a learned skill is a real skill --")
check("it shows up in the summary", "draw-comparison-chart" in ga.skills_summary(), True)
check("and can be loaded", "make_chart" in ga.use_skill("draw-comparison-chart"), True)

print("\n-- you can read and edit them --")
text = ga.skills_as_text(ga.load_skills()["skills"])
check("the heading names it", text.startswith("## draw-comparison-chart"), True)
check("and says it wrote itself", "(learned automatically)" in text, True)
back, why = ga.skills_from_text(text)
check("it round-trips", [s["name"] for s in back], ["draw-comparison-chart"])
check("keeping the learned flag", back[0].get("learned"), True)
check("and the instructions", back[0]["instructions"], ga.load_skills()["skills"][0]["instructions"])

edited = text.replace("Call make_chart", "Always call make_chart")
back, why = ga.skills_from_text(edited)
check("an edit is kept", "Always call make_chart" in back[0]["instructions"], True)
back, why = ga.skills_from_text("")
check("deleting everything is allowed", (back, why), ([], ""))

print("\n-- a mangled edit is refused, not silently saved --")
for bad, why in [
    ("loose text\n## a-skill\ndesc\ninstructions", "before the first"),
    ("## Not A Name\ndesc\nsteps", "not a usable skill name"),
    ("## dupe\nd\ns\n\n## dupe\nd\ns", "twice"),
    ("## lonely\njust a description", "no instructions"),
]:
    parsed, reason = ga.skills_from_text(bad)
    check(f"refused: {why}", (parsed, why in reason), (None, True))

print("\n-- the app decides when, not the model --")
src = APP.read_text()
check("recording happens after a turn finishes", "self.maybe_learn_skill(trace" in src, True)
check("a turn with a failed change teaches nothing", "if unfinished or not trace" in src, True)
check("so does one where permission was refused",
      "did not grant permission" in src.split("def maybe_learn_skill")[1][:400], True)
check("the draft is validated before it is saved",
      "accept_learned_skill(draft" in src, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
