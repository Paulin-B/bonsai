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

box = Path(tempfile.mkdtemp(prefix="bonsai-endpoints-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"

A = "http://localhost:8085/v1/chat/completions"
B = "http://localhost:8087/v1/chat/completions"

print("\n-- a plain single-server setup still works --")
ga.save_settings({**ga.DEFAULTS, "server_url": A, "model": ""})
check("one endpoint is synthesised", len(ga.endpoints()), 1)
check("pointing at the configured server", ga.endpoints()[0]["url"], A)
check("so nothing downstream needs a special case",
      set(ga.endpoints()[0]), {"name", "url", "model", "compose"})

print("\n-- several named servers --")
ga.save_settings({**ga.DEFAULTS, "server_url": A, "endpoints": [
    {"name": "3060", "url": A, "model": ""},
    {"name": "3080 Ti", "url": B, "model": ""}]})
check("both are listed", [e["name"] for e in ga.endpoints()], ["3060", "3080 Ti"])

print("\n-- malformed entries are dropped, not trusted --")
ga.save_settings({**ga.DEFAULTS, "server_url": A, "endpoints": [
    {"name": "good", "url": B},
    {"name": "no url"},
    {"url": "not-a-url"},
    "a bare string"]})
check("only the usable one survives", [e["name"] for e in ga.endpoints()], ["good"])
ga.save_settings({**ga.DEFAULTS, "server_url": A, "endpoints": [{"url": "nonsense"}]})
check("and if none survive it falls back to server_url",
      ga.endpoints()[0]["url"], A)

print("\n-- the text form round-trips --")
items = [{"name": "3060", "url": A, "model": "", "compose": ""},
         {"name": "Ollama", "url": B, "model": "qwen3:32b", "compose": ""}]
text = ga.endpoints_as_text(items)
check("a model is only written when set", text.count("|"), 3)
back, why = ga.endpoints_from_text(text)
check("it comes back the same", back, items)
check("with no complaint", why, "")
check("blank lines are ignored", ga.endpoints_from_text("\n\n" + text)[0], items)
check("an empty list is allowed", ga.endpoints_from_text(""), ([], ""))

print("\n-- each server can name the compose file that starts it --")
# One compose file stopped being the answer the moment there was more than one server.
withfile = [{"name": "30B", "url": B, "model": "", "compose": "/srv/big.yml"}]
text = ga.endpoints_as_text(withfile)
check("it is written out", "/srv/big.yml" in text, True)
check("and read back", ga.endpoints_from_text(text)[0], withfile)
check("the empty model slot is kept so the columns line up", text.count("|"), 3)

ga.save_settings({**ga.DEFAULTS, "server_url": B, "compose_path": "/srv/fallback.yml",
                  "endpoints": [{"name": "small", "url": A, "compose": "/srv/small.yml"},
                                {"name": "big", "url": B, "compose": "/srv/big.yml"}]})
check("the file for the server in use is chosen", ga.compose_for(), "/srv/big.yml")
check("and for another named one", ga.compose_for(A), "/srv/small.yml")
ga.save_settings({**ga.DEFAULTS, "server_url": B, "compose_path": "/srv/fallback.yml",
                  "endpoints": [{"name": "big", "url": B}]})
check("a server with no file of its own falls back to the single setting",
      ga.compose_for(), "/srv/fallback.yml")
ga.save_settings({**ga.DEFAULTS, "server_url": B, "compose_path": "/srv/fallback.yml",
                  "endpoints": []})
check("and so does a one-server setup", ga.compose_for(), "/srv/fallback.yml")

print("\n-- a malformed line is refused rather than silently dropped --")
for bad, why_part in [
    ("just a name", "at least"),
    (" | http://x/y", "no name"),
    ("Name | ftp://nope", "not a URL"),
    (f"Same | {A}\nSame | {B}", "twice"),
]:
    parsed, why = ga.endpoints_from_text(bad)
    check(f"refused: {why_part}", (parsed, why_part in why), (None, True))

print("\n-- in the window --")
ga.save_settings({**ga.DEFAULTS, "server_url": A, "vault_path": "",
                  "briefing_on_open": False, "briefing_enabled": False,
                  "endpoints": [{"name": "3060", "url": A, "model": ""},
                                {"name": "3080 Ti", "url": B, "model": ""}]})
ga.Bonsai.start_docker = lambda self: None
# No server is running in a test, so nothing is asked over the network.
real_models = ga.available_models
ga.available_models = lambda url, timeout=4: []
app = ga.QApplication(sys.argv)
win = ga.Bonsai(); win.resize(1100, 700); win.show()
def settle(n=4):
    for _ in range(n): app.processEvents()
settle()

try:
    win.refresh_models()
    settle()
    labels = [win.model_picker.itemText(i) for i in range(win.model_picker.count())]
    check("both servers are offered", labels, ["3060", "3080 Ti"])
    check("even with every server unreachable", win.model_picker.count(), 2)
    check("and the one in use is selected", win.model_picker.currentText(), "3060")

    win.context_size = 32768
    win.on_model_chosen(1)
    settle()
    check("choosing the other one moves the server", win.settings["server_url"], B)
    check("the context reading is cleared, since it came from the old one",
          win.context_size, 0)
    check("and the label says so", win.context_label.text(), "context: -")

    win.on_model_chosen(0)
    settle()
    check("and back again", win.settings["server_url"], A)

    print("\n-- a server that lists several models offers each of them --")
    ga.available_models = lambda url, timeout=4: ["qwen3:32b", "llama3:70b"]
    win.refresh_models()
    settle()
    labels = [win.model_picker.itemText(i) for i in range(win.model_picker.count())]
    check("the servers are still there", labels[:2], ["3060", "3080 Ti"])
    check("with a row per model under the live one",
          [l for l in labels if "·" in l],
          ["3060 · qwen3:32b", "3060 · llama3:70b"])
    win.on_model_chosen(labels.index("3060 · llama3:70b"))
    settle()
    check("picking one sets the model", win.settings["model"], "llama3:70b")
    check("without moving server", win.settings["server_url"], A)

    ga.available_models = lambda url, timeout=4: ["only-one"]
    win.refresh_models()
    settle()
    check("a single-model server adds no rows",
          [win.model_picker.itemText(i) for i in range(win.model_picker.count())],
          ["3060", "3080 Ti"])
finally:
    ga.available_models = real_models

print("\n-- a server that is not running says so, not urllib3 --")
ga.save_settings({**ga.DEFAULTS, "server_url": B, "endpoints": [
    {"name": "3060 (always on)", "url": A, "model": ""},
    {"name": "Both cards - 30B", "url": B, "model": ""}]})
said = ga.unreachable_server(B)
check("it names the server as the picker shows it", "'Both cards - 30B'" in said, True)
check("and the address, so a typo is visible", B in said, True)
check("it says what is most likely wrong", "not running" in said, True)
check("and points at the one other server", "switch to '3060 (always on)'" in said, True)
check("without mentioning the one that failed", said.count("Both cards - 30B"), 1)

ga.save_settings({**ga.DEFAULTS, "server_url": B, "endpoints": [
    {"name": "a", "url": A}, {"name": "b", "url": B}, {"name": "c", "url": "http://x/y"}]})
many = ga.unreachable_server(B)
check("with several alternatives it lists them", "a, c" in many, True)

ga.save_settings({**ga.DEFAULTS, "server_url": B, "endpoints": []})
alone = ga.unreachable_server(B)
check("with nowhere to switch to it points at Settings", "Settings" in alone, True)
check("and does not offer a switch that does not exist", "switch to" in alone, False)

src = APP.read_text()
check("a refused connection is caught before the generic handler",
      src.index("except requests.exceptions.ConnectionError:")
      < src.index('self.failed.emit(f"Error: {exc}")'), True)

print("\n-- switching stops auto mode, which belonged to the old model --")
win.auto_running = True
win.on_model_chosen(1)
settle()
check("auto mode stopped", win.auto_running, False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
