import importlib.util, os, sys
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

MINE = os.getpid()
MONITORS = [{"id": 0, "name": "DP-4", "focused": False},
            {"id": 1, "name": "DP-5", "focused": True}]
def fake(clients):
    def _hypr(*args):
        return MONITORS if args[0] == "monitors" else clients
    return _hypr

print("\n-- Bonsai never captures itself --")
ga._hypr = fake([
    {"pid": MINE, "class": "gui_assistant.py", "title": "Bonsai",
     "at": [0, 0], "size": [1100, 720], "monitor": 1, "focusHistoryID": 0},
    {"pid": 4242, "class": "codium", "title": "flow_field.gd",
     "at": [2560, 37], "size": [1266, 1394], "monitor": 1, "focusHistoryID": 1},
])
w = ga.target_window()
check("picks the editor, not Bonsai", w["class"], "codium")
check("region is the editor at native size", ga.window_region(w), "2560,37 1266x1394")
check("active-window grabs that region",
      ga.capture_args("active-window", "/t.png"), ["grim", "-g", "2560,37 1266x1394", "/t.png"])
check("active-monitor grabs the editor's monitor",
      ga.capture_args("active-monitor", "/t.png"), ["grim", "-o", "DP-5", "/t.png"])
check("all still spans everything", ga.capture_args("all", "/t.png"), ["grim", "/t.png"])

print("\n-- ordering, hidden and zero-size windows --")
ga._hypr = fake([
    {"pid": 1, "class": "steam", "at": [0, 0], "size": [800, 600], "monitor": 0, "focusHistoryID": 3},
    {"pid": 2, "class": "brave", "at": [0, 0], "size": [900, 700], "monitor": 0, "focusHistoryID": 1},
    {"pid": 3, "class": "thunar", "at": [0, 0], "size": [700, 500], "monitor": 1, "focusHistoryID": 2},
])
check("most recently focused wins", ga.target_window()["class"], "brave")
ga._hypr = fake([
    {"pid": 1, "class": "ghost", "at": [0, 0], "size": [0, 0], "monitor": 0, "focusHistoryID": 0},
    {"pid": 2, "class": "tucked", "hidden": True, "at": [0, 0], "size": [900, 700],
     "monitor": 0, "focusHistoryID": 1},
    {"pid": 3, "class": "real", "at": [10, 20], "size": [640, 480], "monitor": 0, "focusHistoryID": 2},
])
check("zero-size and hidden windows skipped", ga.target_window()["class"], "real")
check("monitor resolved from the window", ga.output_for(ga.target_window()), "DP-4")

print("\n-- degrades instead of failing --")
ga._hypr = lambda *a: None                       # not Hyprland at all
check("no compositor -> whole desktop",
      ga.capture_args("active-window", "/t.png"), ["grim", "/t.png"])
ga._hypr = fake([])                              # Hyprland, but nothing to capture
check("no windows -> focused monitor",
      ga.capture_args("active-window", "/t.png"), ["grim", "-o", "DP-5", "/t.png"])
ga._hypr = fake([{"pid": MINE, "class": "self", "at": [0, 0], "size": [10, 10],
                  "monitor": 1, "focusHistoryID": 0}])
check("only Bonsai open -> falls back to its monitor",
      ga.capture_args("active-window", "/t.png"), ["grim", "-o", "DP-5", "/t.png"])

print("\n-- observer URL honours the service switch --")
def observer_url(config):
    url = (config.get("observer_url") or "").strip()
    if not (config.get("services") or {}).get("bonsai-observer", False):
        url = ""
    return url or config.get("server_url")
MAIN, OBS = "http://localhost:8085/v1/chat/completions", "http://localhost:8086/v1/chat/completions"
check("service off -> main server, not a dead port",
      observer_url({"observer_url": OBS, "server_url": MAIN,
                    "services": {"bonsai-observer": False}}), MAIN)
check("service on -> the observer",
      observer_url({"observer_url": OBS, "server_url": MAIN,
                    "services": {"bonsai-observer": True}}), OBS)
check("blank url -> main server",
      observer_url({"observer_url": "", "server_url": MAIN,
                    "services": {"bonsai-observer": True}}), MAIN)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
