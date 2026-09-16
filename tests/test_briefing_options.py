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

box = Path(tempfile.mkdtemp(prefix="bonsai-brief-"))
for n in ["SETTINGS_FILE","BRIEFING_FILE","BRIEFING_SEEN_FILE","INTERESTS_FILE",
          "MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","TRUSTED_PATHS_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.save_settings(ga.DEFAULTS)

print("\n-- which kinds of result to gather --")
check("the default is news and videos", ga.briefing_kinds(), ["news", "videos"])
ga.save_settings({**ga.DEFAULTS, "briefing_kinds": ["news", "images", "general"]})
check("a chosen set is honoured", ga.briefing_kinds(), ["news", "images", "general"])
ga.save_settings({**ga.DEFAULTS, "briefing_kinds": ["news", "nonsense"]})
check("a kind SearXNG has no category for is dropped", ga.briefing_kinds(), ["news"])
ga.save_settings({**ga.DEFAULTS, "briefing_kinds": []})
check("choosing nothing falls back rather than fetching nothing",
      ga.briefing_kinds(), ["news", "videos"])
ga.save_settings({**ga.DEFAULTS, "briefing_kinds": ["nonsense"]})
check("and so does choosing only nonsense", ga.briefing_kinds(), ["news", "videos"])

print("\n-- remembering what has already been shown --")
ga.save_settings(ga.DEFAULTS)
check("nothing seen to begin with", ga.load_briefing_seen(), {})
ga.remember_briefing_urls(["https://a.example/one", "https://b.example/two"])
check("both remembered", sorted(ga.load_briefing_seen()),
      ["https://a.example/one", "https://b.example/two"])
ga.remember_briefing_urls(["https://c.example/three"])
check("adding does not forget the earlier ones", len(ga.load_briefing_seen()), 3)

import datetime as _dt
stale = (_dt.datetime.now() - _dt.timedelta(days=ga.BRIEFING_SEEN_DAYS + 5)).date().isoformat()
ga.save_json(ga.BRIEFING_SEEN_FILE, {"urls": {"https://old.example/x": stale,
                                              "https://new.example/y": _dt.date.today().isoformat()}})
ga.remember_briefing_urls([])
check("something old enough to be worth seeing again is dropped",
      sorted(ga.load_briefing_seen()), ["https://new.example/y"])

ga.save_json(ga.BRIEFING_SEEN_FILE, {"urls": {}})
ga.remember_briefing_urls([f"https://x.example/{i}" for i in range(ga.BRIEFING_SEEN_CAP + 50)])
check("the list cannot grow without limit",
      len(ga.load_briefing_seen()) <= ga.BRIEFING_SEEN_CAP, True)
ga.forget_briefing_seen()
check("and it can be cleared", ga.load_briefing_seen(), {})

print("\n-- a fetch skips what you have already been shown --")
CANNED = {
    ("Godot", "news"): [{"title": "Godot 4.5 is out", "url": "https://a.example/one"},
                        {"title": "Godot shaders guide", "url": "https://b.example/two"}],
    ("Godot", "videos"): [{"title": "Godot in 100 seconds", "url": "https://c.example/three"}],
}
def fake_query(topic, kind, per_topic, time_range):
    fake_query.asked.append((topic, kind, time_range))
    return [{**item, "source": "test", "published": "", "summary": "",
             "thumbnail": "", "kind": kind} for item in CANNED.get((topic, kind), [])]
fake_query.asked = []

real_query, real_pause = ga.store._briefing_query, ga.store.BRIEFING_PAUSE
ga.store._briefing_query, ga.store.BRIEFING_PAUSE = fake_query, 0
try:
    ga.save_settings({**ga.DEFAULTS, "briefing_no_repeats": True,
                      "briefing_kinds": ["news", "videos"], "briefing_time_range": "week"})
    first = ga.fetch_briefing(["Godot"])
    urls = [i["url"] for t in first["topics"] for i in t["items"]]
    check("everything comes through the first time", len(urls), 3)
    check("nothing was counted as a repeat", first["repeats_skipped"], 0)
    check("the chosen window was used", {r for _, _, r in fake_query.asked}, {"week"})
    check("and both chosen kinds were asked for",
          sorted({k for _, k, _ in fake_query.asked}), ["news", "videos"])

    fake_query.asked.clear()
    second = ga.fetch_briefing(["Godot"])
    check("the second pull shows nothing already read",
          [i["url"] for t in second["topics"] for i in t["items"]], [])
    check("and says how many it skipped", second["repeats_skipped"], 3)

    ga.forget_briefing_seen()
    third = ga.fetch_briefing(["Godot"])
    check("forgetting brings them back",
          len([i for t in third["topics"] for i in t["items"]]), 3)

    ga.save_settings({**ga.DEFAULTS, "briefing_no_repeats": False,
                      "briefing_kinds": ["news"]})
    fake_query.asked.clear()
    fourth = ga.fetch_briefing(["Godot"])
    check("with repeats allowed, they come back",
          len([i for t in fourth["topics"] for i in t["items"]]), 2)
    check("and only the chosen kind is fetched",
          sorted({k for _, k, _ in fake_query.asked}), ["news"])
finally:
    ga.store._briefing_query, ga.store.BRIEFING_PAUSE = real_query, real_pause

print("\n-- the settings page --")
app = ga.QApplication(sys.argv)
ga.save_settings({**ga.DEFAULTS, "briefing_time_range": "month"})
dialog = ga.SettingsDialog(ga.settings())
check("there is a Briefing page", "Briefing" in dialog.PAGES, True)
check("a box per kind", sorted(dialog.kind_boxes), sorted(ga.BRIEFING_ALL_KINDS))
check("the window is shown in words",
      dialog.fields["briefing_time_range"].currentText(), "Past month")
for shown, stored in ga.BRIEFING_RANGE_LABELS.items():
    one = ga.SettingsDialog({**ga.settings(), "briefing_time_range": stored})
    check(f"  {stored!r} round-trips as {shown!r}",
          one.fields["briefing_time_range"].currentText(), shown)
    one.save()
    check(f"  ...and saves back as {stored!r}",
          one.result_settings["briefing_time_range"], stored)

dialog.kind_boxes["images"].setChecked(True)
dialog.kind_boxes["videos"].setChecked(False)
dialog.fields["briefing_no_repeats"].setChecked(False)
dialog.save()
check("the chosen kinds are saved",
      sorted(dialog.result_settings["briefing_kinds"]), ["images", "news"])
check("and the repeat switch", dialog.result_settings["briefing_no_repeats"], False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
