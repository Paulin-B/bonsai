import json
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

box = Path(tempfile.mkdtemp(prefix="bonsai-budget-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","SCREEN_LOG_FILE",
          "INTERESTS_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)

def tokens(names=None):
    return len(json.dumps(ga.build_tool_schemas(names))) // 4

ALL = {t["function"]["name"] for t in ga.build_tool_schemas()}

print("\n-- nothing is lost: every tool is still reachable --")
check("no tool is orphaned - each is core or has triggers",
      ALL - ga.CORE_TOOL_NAMES - set(ga.TOOL_TRIGGERS), set())
check("no trigger names a tool that doesn't exist",
      set(ga.TOOL_TRIGGERS) - ALL, set())
check("core and triggered never overlap",
      ga.CORE_TOOL_NAMES & set(ga.TOOL_TRIGGERS), set())
check("asking for everything still gives everything", len(ga.build_tool_schemas()), len(ALL))

print("\n-- the core set goes out no matter what is asked --")
for prompt in ("", "hello", "what is 2+2", "fix the bug"):
    check(f"core present for {prompt!r}",
          ga.CORE_TOOL_NAMES <= ga.relevant_tools(prompt), True)
check("reading, editing and running are always there",
      {"read_file", "edit", "file_op", "run"} <= ga.CORE_TOOL_NAMES, True)

print("\n-- the right extras load for the right requests --")
def extras(prompt):
    return sorted(ga.relevant_tools(prompt) - ga.CORE_TOOL_NAMES)
check("a rename", "usages" in extras("rename spawn_task everywhere"), True)
check("the vault", {"save_note", "search_vault"} <= set(extras("save this in obsidian")), True)
check("pictures", "search_images" in extras("find me a picture of a bonsai"), True)
check("earlier chats", "history" in extras("what did you tell me last time"), True)

print("\n-- and stay out otherwise --")
check("plain questions load nothing extra", extras("what is my cpu"), [])
check("editing a shader loads nothing extra", extras("fix the tint in gbuffers_basic.fsh"), [])

print("\n-- plurals count, prefixes do not --")
check("'notes' matches 'note'", ga._mentions("save my notes", "note"), True)
check("'graphics' would not match 'graph'", ga._mentions("my graphics card", "graph"), False)
check("'opener' does not match 'open'", ga._mentions("the opener", "open"), False)
check("phrases still match", ga._mentions("what did you tell me last time", "last time"), True)

print("\n-- the saving, measured --")
full = tokens()
check("all schemas are the number I claimed", 2500 < full < 3300, True)
sizes = [tokens(ga.relevant_tools(p)) for p in (
    "fix the shader", "make a chart of my disk usage", "what gpu do I have",
    "write a pdf report with a chart", "rename this function everywhere")]
average = sum(sizes) // len(sizes)
print(f"     all tools ~{full} tokens; typical request ~{average}")
check("a typical request costs meaningfully less", average < full * 0.80, True)
check("even the heaviest request saves something", max(sizes) < full, True)

print("\n-- a dormant tool is named, so it never becomes invisible --")
docs = ga.build_system_prompt("fix the shader")
for name in ("SEARCH_IMAGES", "SAVE_NOTE", "HISTORY"):
    check(f"{name} is listed as available", name in docs, True)
check("with how to call it", "[TOOL: NAME: argument]" in docs, True)
check("a request that loads a tool does not list it as dormant",
      "save_note" in ga.dormant_tools("save this to obsidian"), False)

print("\n-- tools whose absence makes it REINVENT the capability are never withheld --")
# Measured: with make_chart withheld it wrote a Pillow script and burned the turn.
for name in ("make_chart", "make_pdf", "launch", "look"):
    check(f"{name} is always present", name in ga.CORE_TOOL_NAMES, True)
    check(f"{name} is not merely triggered", name in ga.TOOL_TRIGGERS, False)
check("a chart is available even when the word 'chart' never appears",
      "make_chart" in ga.relevant_tools(
          "I want a picture showing how my disk is divided up"), True)
check("so is a launcher, however the request is phrased",
      "launch" in ga.relevant_tools("put kitty on my screen"), True)

print("\n-- using a dormant tool loads it for the rest of the turn --")
worker = ga.Worker("fix the shader", False, [], dict(ga.settings()))
check("it starts without the image search", "search_images" in worker.active_tools, False)
check("so its schema is not sent",
      any(t["function"]["name"] == "search_images"
          for t in ga.build_tool_schemas(worker.active_tools)), False)
worker.active_tools.add("SEARCH_IMAGES".lower())   # what the loop does after a call
check("once used, it is loaded", "search_images" in worker.active_tools, True)
check("and its schema goes out from then on",
      any(t["function"]["name"] == "search_images"
          for t in ga.build_tool_schemas(worker.active_tools)), True)
src = APP.read_text()
check("the loop really does add every tool it ran",
      "self.active_tools.add(name.lower())" in src, True)
check("and the schemas sent are the active set",
      "build_tool_schemas(self.active_tools)" in src, True)

print("\n-- the text parser still accepts a tool whose schema was withheld --")
name, argument = ga.extract_tool_call("[TOOL: SEARCH_IMAGES: a bonsai tree]")
check("SEARCH_IMAGES parses even though it was not sent", name, "SEARCH_IMAGES")
name, argument = ga.extract_tool_call("[TOOL: HISTORY: what we said about shaders]")
check("so does HISTORY", name, "HISTORY")

print("\n-- web search off removes it rather than leaving a dead schema --")
ga.save_settings({**ga.DEFAULTS, "web_search_enabled": False})
check("no search schema", any(t["function"]["name"] == "search"
                              for t in ga.build_tool_schemas()), False)
ga.save_settings(ga.DEFAULTS)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
