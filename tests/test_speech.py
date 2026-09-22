"""Saying it out loud - and mostly, deciding what not to say.

Making sound is finding an engine and handing it text. The design problem is which
text: the model writes markdown, fenced code, absolute paths and emoji, and read aloud
those become "backtick def main backtick" and half a minute of punctuation. A reply
spoken badly is worse than one not spoken, because a listener cannot skim past it.

None of this needs an engine installed, which is the point - the decisions are all in
speakable().
"""
import sys
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)

print("\n-- code is named, not read out --")
reply = ("Here is the fix:\n\n```python\ndef main():\n    return 1\n```\n\n"
         "That returns one.")
spoken = ga.speakable(reply)
check("the prose survives", "That returns one." in spoken, True)
check("the code does not", "def main" in spoken, False)
check("but it says there was some", "1 code block" in spoken, True)
two = ga.speakable("One:\n```\na\n```\nTwo:\n```\nb\n```\nDone.")
check("counted, plural", "2 code blocks" in two, True)
check("inline code keeps its word, loses its backticks",
      ga.speakable("Run `pytest` now."), "Run pytest now.")

print("\n-- a path is a filename, not a recital --")
check("long paths collapse",
      ga.speakable("I changed /home/paulinb/Projects/bonsai-public/bonsai/app.py."),
      "I changed app.py.")
check("so do home-relative ones",
      ga.speakable("It is in ~/Projects/bonsai-public/bridges/factorio.py."),
      "It is in factorio.py.")
check("a bare word is untouched", ga.speakable("Check app.py."), "Check app.py.")
check("a link is not spelled out",
      ga.speakable("See https://example.com/a/b?c=1 for more."),
      "See a link for more.")

print("\n-- markdown furniture goes --")
check("headings", ga.speakable("## What I did\nNothing."), "What I did\nNothing.")
check("bullets", ga.speakable("- one\n- two"), "one\ntwo")
check("numbers", ga.speakable("1. first\n2. second"), "first\nsecond")
check("emphasis keeps the word",
      ga.speakable("That is **really** important."), "That is really important.")
check("quotes", ga.speakable("> quoted line"), "quoted line")
check("tables are dropped whole",
      ga.speakable("Results:\n| a | b |\n|---|---|\n| 1 | 2 |\nDone."),
      "Results:\nDone.")
check("emoji are not read", ga.speakable("Done! 🎮✨ Nice."), "Done! Nice.")

print("\n-- long replies are previewed, not truncated mid-word --")
long_reply = ("First sentence here. " * 40).strip()
spoken = ga.speakable(long_reply, limit=200)
check("it is shortened", len(spoken) < len(long_reply), True)
check("  ...and says where the rest is", spoken.endswith("There is more on screen."), True)
check("  ...ending on a sentence, not a fragment",
      spoken.replace(" There is more on screen.", "").endswith("."), True)
check("a short reply is left exactly alone",
      ga.speakable("All done.", limit=200), "All done.")

print("\n-- nothing worth saying is said --")
for quiet in ["", None, "```\njust code\n```", "| a |\n|---|", "🎉"]:
    spoken = ga.speakable(quiet)
    check(f"quiet for {quiet!r}", spoken == "" or "code block" in spoken, True)

print("\n-- the queue, without an engine present --")
speaker = ga.Speaker()
speaker.say("```\ncode only\n```")
check("something with prose in it is queued", speaker.lines.qsize() >= 1, True)
speaker.say("")
check("an empty line is not", speaker.lines.qsize(), 1)
speaker.silence()
check("silence empties the queue", speaker.lines.qsize(), 0)

print("\n-- it explains itself rather than failing quietly --")
why = ga.why_silent()
check("there is a reason", bool(why), True)
check("  ...naming what to install", "piper" in why and "espeak" in why, True)
check("engine is None with nothing installed", ga.available_engine(), None)
check("a player is found on this machine", ga.player_argv() is not None, True)

print("\n-- and the setting that gates it --")
check("speaking is off by default", ga.DEFAULTS["speak_replies"], False)
check("volunteered lines are on once speaking is", ga.DEFAULTS["speak_unprompted"], True)
check("there is a rate", ga.DEFAULTS["speech_rate"], 1.0)
check("and a spoken length", ga.DEFAULTS["max_spoken_chars"], 600)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
