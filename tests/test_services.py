import importlib.util, tempfile, sys
from pathlib import Path
from bonsai_under_test import APP, load
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

print("\n-- only services the compose file defines are ever asked for --")
# Naming one it does not have fails the whole command with "no such service", which is
# what happened the first time a model-only compose file was asked for searxng too.
import tempfile as _tf
from pathlib import Path as _P
_box = _P(_tf.mkdtemp(prefix="bonsai-compose-"))

FULL = """services:
  bonsai-api:
    image: x
    environment:
      - A=1
    ports:
      - "1:1"
  searxng:
    image: y
    volumes:
      - ./a:/b
  bonsai-observer:
    image: z

volumes:
  shared:
"""
ONLY_API = "services:\n  bonsai-api:\n    image: x\n    environment:\n      - A=1\n"
(_box / "full.yml").write_text(FULL)
(_box / "api.yml").write_text(ONLY_API)

check("every service is found", ga.compose_services(_box / "full.yml"),
      ["bonsai-api", "searxng", "bonsai-observer"])
check("a model-only file offers just the one", ga.compose_services(_box / "api.yml"),
      ["bonsai-api"])
check("nested keys are not mistaken for services",
      "environment" in ga.compose_services(_box / "full.yml"), False)
check("nor is a later top-level block",
      "volumes" in ga.compose_services(_box / "full.yml"), False)
check("a file that is not there gives nothing", ga.compose_services("/nope/x.yml"), [])

# The same answers without PyYAML, since it is an optional dependency.
_real_yaml = ga.store.yaml
ga.store.yaml = None
try:
    check("the text fallback agrees on a full file",
          ga.compose_services(_box / "full.yml"),
          ["bonsai-api", "searxng", "bonsai-observer"])
    check("and on a model-only one", ga.compose_services(_box / "api.yml"),
          ["bonsai-api"])
finally:
    ga.store.yaml = _real_yaml

print("\n-- stopping names only what the file defines, too --")
# Stop deliberately asks for every service rather than the enabled ones, so that a
# service switched off while running is not orphaned. It still must not name one the
# file does not have: that fails the whole command, so Stop reported an error and left
# everything running.
src = APP.read_text()
stop_body = src.split("def stop_docker")[1].split("def on_docker_stopped")[0]
check("stop consults the compose file", "compose_services(path)" in stop_body, True)
check("and falls back to every known service when it cannot read one",
      "or list(DOCKER_SERVICES)" in stop_body, True)
check("it no longer spreads DOCKER_SERVICES blindly",
      "*DOCKER_SERVICES" in stop_body, False)
check("but still stops more than just the enabled ones",
      "active_services" in stop_body, False)

print("\n-- so the window asks for the intersection --")
src = APP.read_text()
check("active_services consults the compose file",
      "compose_services(compose_for(" in src, True)
check("and asks for everything when the file cannot be read",
      "if defined else asked" in src, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
