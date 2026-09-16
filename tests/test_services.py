import importlib.util, tempfile, sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-svc-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
ga.CHATS_DIR = box / "chats"
started = []
ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
w = ga.Bonsai()

print("\n-- which services get started --")
check("default: model + search, no observer",
      (w.settings["services"]["bonsai-api"], w.settings["services"]["searxng"],
       w.settings["services"]["bonsai-observer"]), (True, True, False))
check("default active list", w.active_services(), ["bonsai-api", "searxng"])

w.settings["services"] = {"bonsai-api": True, "searxng": False, "bonsai-observer": False}
check("search off -> model only", w.active_services(), ["bonsai-api"])

w.settings["services"] = {"bonsai-api": False, "searxng": False, "bonsai-observer": False}
check("the model server cannot be switched off", w.active_services(), ["bonsai-api"])

w.settings["services"] = {"bonsai-api": True, "searxng": True, "bonsai-observer": True}
check("everything on", w.active_services(), ["bonsai-api", "searxng", "bonsai-observer"])

print("\n-- the compose command that actually runs --")
w.settings["compose_path"] = str(box / "docker-compose.yml")
(box / "docker-compose.yml").write_text("services: {}\n")
w.settings["services"] = {"bonsai-api": True, "searxng": False, "bonsai-observer": False}
args = w.compose_args("up", "-d", *w.active_services())
check("only enabled services in the up command",
      [a for a in args if a in ga.DOCKER_SERVICES], ["bonsai-api"])
stop_args = w.compose_args("stop", *ga.DOCKER_SERVICES)
check("stop still covers every service, so none is orphaned",
      [a for a in stop_args if a in ga.DOCKER_SERVICES],
      ["bonsai-api", "searxng", "bonsai-observer"])

print("\n-- proactive mode still works with the observer container off --")
check("observer falls back to the main server when no URL is set",
      (w.settings.get("observer_url") or "").strip() or w.settings["server_url"],
      w.settings["server_url"])

print("\n-- the settings dialog exposes them --")
dialog = ga.SettingsDialog(w.settings, w)
check("a checkbox per service", sorted(dialog.service_boxes), sorted(ga.DOCKER_SERVICES))
check("the required one is locked on",
      (dialog.service_boxes["bonsai-api"].isChecked(),
       dialog.service_boxes["bonsai-api"].isEnabled()), (True, False))
check("optional ones are editable", dialog.service_boxes["searxng"].isEnabled(), True)
dialog.service_boxes["bonsai-observer"].setChecked(True)
dialog.save()
check("save round-trips the choices",
      dialog.result_settings["services"],
      {"bonsai-api": True, "searxng": False, "bonsai-observer": True})

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
