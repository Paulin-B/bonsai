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

box = Path(tempfile.mkdtemp(prefix="bonsai-look-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE"]:
    setattr(ga, n, box / n.lower())
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})
ga.save_json(ga.MEMORY_FILE, {"core": ["Builds a Godot factory sim"],
                              "archival": ["Two 2560x1440 monitors side by side."]})

print("\n-- the reply that started this --")
real = ("I don't have any information about your monitor setup. I can't see your "
        "screen or hardware unless you share a screenshot.")
check("flagged when nothing was searched", ga.unsearched_memory(real, "LIST_DIR: /x -> ok"), True)
check("cleared once RECALL has run", ga.unsearched_memory(real, "RECALL: monitors -> ..."), False)

print("\n-- other ways of pleading ignorance about the user --")
for reply in [
    "I don't know your preferred shell, sorry.",
    "I have no record of your GPU setup.",
    "I couldn't find your editor preferences anywhere.",
    "I'm not sure what your usual workflow is.",
]:
    check(f"flagged: {reply[:44]}", ga.unsearched_memory(reply, ""), True)

print("\n-- but not for things RECALL could never answer --")
for reply in [
    "I don't know what that file contains until I read it.",
    "I can't see your screen right now.",
    "The build failed, so I don't have the test results.",
    "I don't have network access to check that.",
    "Your monitors are two 2560x1440 panels, side by side.",
]:
    check(f"not flagged: {reply[:44]}", ga.unsearched_memory(reply, ""), False)

print("\n-- and never when there is nothing archived to find --")
ga.save_json(ga.MEMORY_FILE, {"core": ["Builds a Godot factory sim"], "archival": []})
check("empty archive means the denial is honest", ga.unsearched_memory(real, ""), False)

print("\n-- the correction is one-shot and honest about the count --")
text = APP.read_text()
check("five one-shot flags", "corrected = pressed = reminded = searched = recalled = False" in text, True)
check("recalled set before continue", text.count("recalled = True"), 1)
check("nudge names how many are stored", "fact(s) in archival memory" in text, True)

print("\n-- the prompt argues the point --")
ga.save_json(ga.MEMORY_FILE, {"core": ["Builds a Godot factory sim"],
                              "archival": ["Two 2560x1440 monitors side by side."]})
check("a small archive is shown in full rather than hidden behind a tool",
      "2560" in ga.build_system_prompt("what monitors do I have"), True)
# Only once it is too large to inline does the search advice apply.
ga.save_json(ga.MEMORY_FILE, {"core": [], "archival":
    [f"Padding fact {i}: a long stored detail well past the inline budget." * 2
     for i in range(200)]})
big_prompt = ga.build_system_prompt("anything")
check("a large archive is retrieved from, not dumped",
      "retrieved for this question" in big_prompt, True)
check("  ...and still points at recall for the rest", "search with recall" in big_prompt, True)
check("  ...suggesting synonyms", "graphics card gpu video card" in big_prompt, True)

print("\n-- singular and plural find each other --")
ga.save_json(ga.MEMORY_FILE, {"core": [], "archival": [
    "Sam's GPU 0 is an RTX 3060 and GPU 1 an RTX 3080 Ti.",
    "Two 2560x1440 monitors side by side.",
    "Prefers the fish shell."]})
for query, needle in [("GPUs", "3060"), ("GPU", "3060"), ("monitor", "2560"),
                      ("monitors", "2560"), ("shells", "fish"), ("shell", "fish")]:
    check(f"'{query}' finds it", needle in ga.search_memory(query), True)
# With a small archive there is no such thing as a miss: everything is shown and
# the model judges relevance, because lexical matching cannot do synonyms.
check("an unrelated query still returns the archive",
      "3060" in ga.search_memory("penguins"), True)

print("\n-- and the same for earlier chats --")
import tempfile as _tf
chatbox = Path(_tf.mkdtemp(prefix="bonsai-look-chats-"))
ga.CHATS_DIR = chatbox / "chats"
ga.CHAT_INDEX_FILE = chatbox / "index.json"
cid = ga.new_chat(); ga.title_chat(cid, "Belts")
ga.save_chat(cid, [{"role": "assistant", "content": "The belt reserves a slot first."}])
check("'belts' finds 'belt'", "reserves a slot" in ga.search_chats("belts", "other"), True)
check("'slots' finds 'slot'", "reserves a slot" in ga.search_chats("slots", "other"), True)

print("\n-- a small archive is handed over whole, so synonyms work --")
ga.save_json(ga.MEMORY_FILE, {"core": [], "archival": [
    "Sam's GPU 0 is an RTX 3060 and GPU 1 an RTX 3080 Ti.",
    "Two 2560x1440 monitors side by side.",
    "Prefers the fish shell.",
    "Runs CachyOS with Hyprland."]})
for query in ["graphics card", "screens", "terminal", "window manager", "operating system"]:
    out = ga.search_memory(query)
    answerable = any(k in out for k in ("3060", "2560", "fish", "Hyprland"))
    check(f"'{query}' is answerable", answerable, True)
check("says why it showed everything", "whole of it" in ga.search_memory("anything"), True)

print("\n-- an empty archive is stated, not searched --")
ga.save_json(ga.MEMORY_FILE, {"core": ["x"], "archival": []})
check("empty is explicit", ga.search_memory("gpu").startswith("[Archival memory is empty"), True)
check("and says the rest is already in the prompt",
      "already in your prompt" in ga.search_memory("gpu"), True)

print("\n-- a large archive still searches, and a miss shows recent facts --")
big = [f"Fact number {i} about some unrelated topic, padded out a bit." for i in range(120)]
big.append("The belt handshake is two-phase.")
ga.save_json(ga.MEMORY_FILE, {"core": [], "archival": big})
hit = ga.search_memory("handshake")
check("a literal hit still filters", "two-phase" in hit and "Fact number 3 " not in hit, True)
miss = ga.search_memory("zeppelin")
check("a miss does not claim absence", "No fact literally contains" in miss, True)
check("  ...and shows recent facts instead", "Fact number 119" in miss, True)
check("  ...within the budget", len(miss) < 3600, True)

print("\n-- a small archive goes straight into the prompt, no tool needed --")
ga.save_json(ga.MEMORY_FILE, {"core": ["Builds a Godot factory sim"], "archival": [
    "Sam's GPU 0 is an RTX 3060 and GPU 1 an RTX 3080 Ti.",
    "Two 2560x1440 monitors side by side.",
    "Prefers the fish shell.",
    "Runs CachyOS with Hyprland."]})
prompt = ga.build_system_prompt("What graphics card am I using?")
check("the facts are inline", "3060" in prompt and "Hyprland" in prompt, True)
check("labelled as complete", "nothing withheld" in prompt, True)

print("\n-- an oversized archive is retrieved for the question instead --")
padding = [f"Padding fact {i}: an unrelated stored detail, deliberately verbose so the "
           f"archive genuinely exceeds the inline budget." for i in range(200)]
real = ["Sam's GPU 0 is an RTX 3060 and GPU 1 an RTX 3080 Ti.",
        "Two 2560x1440 monitors side by side.",
        "Prefers the fish shell.",
        "Runs CachyOS with Hyprland."]
ga.save_json(ga.MEMORY_FILE, {"core": [], "archival": padding + real})
check("too big to inline", len("\n".join(padding + real)) > ga.ARCHIVAL_INLINE_CHARS, True)
prompt = ga.build_system_prompt("What graphics card am I using?")
check("retrieved rather than inlined", "retrieved for this question" in prompt, True)
check("stays within the retrieval budget", len(prompt) < 26000, True)
check("says how many are stored in total", "older facts are stored in total" in prompt, True)

print("\n-- a literal match is preferred, recency breaks ties --")
got = ga.retrieve_archival("What GPU do I have?", padding + real)
check("the matching fact is first", "RTX 3060" in got[0], True)
got = ga.retrieve_archival("zeppelin cartography", padding + real)  # no lexical overlap
check("with no match, newest come first (the -index bug put oldest first)",
      all(f in got for f in real), True)
check("  ...and not the oldest padding", padding[0] in got, False)

print("\n-- an empty question still returns something --")
check("no question, newest facts", bool(ga.retrieve_archival("", padding + real)), True)
check("empty archive, empty result", ga.retrieve_archival("gpu", []), [])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
