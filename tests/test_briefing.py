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
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE",
          "INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})

print("\n-- interests are tidied and kept --")
saved = ga.save_interests([" Godot engine ", "factory sims", "godot ENGINE", "", "  "])
check("trimmed, de-duplicated, blanks dropped", saved, ["Godot engine", "factory sims"])
check("round-trips", ga.load_interests(), ["Godot engine", "factory sims"])
check("capped", len(ga.save_interests([f"topic {i}" for i in range(40)])), 20)
ga.save_interests(["Godot engine"])

print("\n-- staleness drives refetching --")
check("no cache is stale", ga.briefing_is_stale(), True)
ga.save_json(ga.BRIEFING_FILE, {"fetched": ga.datetime.now().isoformat(timespec="minutes"),
                                "topics": []})
check("fresh cache is not stale", ga.briefing_is_stale(), False)
old = (ga.datetime.now() - ga.timedelta(hours=48)).isoformat(timespec="minutes")
ga.save_json(ga.BRIEFING_FILE, {"fetched": old, "topics": []})
check("old cache is stale", ga.briefing_is_stale(), True)
ga.save_json(ga.BRIEFING_FILE, {"fetched": "not a date", "topics": []})
check("corrupt timestamp is stale, not a crash", ga.briefing_is_stale(), True)

print("\n-- a dead search backend degrades, it does not break startup --")
ga.save_settings({**ga.DEFAULTS, "vault_path": "",
                  "searxng_url": "http://127.0.0.1:9/search"})
ga.BRIEFING_PAUSE = 0
result = ga.fetch_briefing(["Godot engine"])
check("returns a briefing anyway", isinstance(result.get("topics"), list), True)
check("topic present but empty", result["topics"][0]["items"], [])
check("cache still written", ga.load_briefing()["topics"][0]["topic"], "Godot engine")

print("\n-- suggestions come from what is already known --")
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})
proj = box / "factory-game"; proj.mkdir(exist_ok=True)
ga.save_trusted([str(proj)])
(proj / "a.gd").write_text("x" * 300)
ga.record_project_file(str(proj / "a.gd"))
check("project name suggested", any("factory" in s.lower() for s in ga.suggested_interests()), True)

print("\n-- the page --")
app = ga.QApplication(sys.argv)
ga.Bonsai.start_docker = lambda self: None
ga.save_interests(["Godot engine"])
ga.save_json(ga.BRIEFING_FILE, {
    "fetched": ga.datetime.now().isoformat(timespec="minutes"),
    "topics": [{"topic": "Godot engine", "items": [
        {"title": "Godot 4.5 released", "url": "https://example.org/a",
         "source": "reuters", "published": "2026-09-11", "summary": "Notes here.",
         "thumbnail": "", "kind": "news"},
        {"title": "Making a factory sim <b>fast</b>", "url": "https://example.org/b",
         "source": "youtube", "published": "", "summary": "", "thumbnail": "",
         "kind": "videos"}]}]})
w = ga.Bonsai(); w.resize(1480, 800); w.show()
for _ in range(4): app.processEvents()

check("two pages: chat and briefing", w.pages.count(), 2)
check("opens on the briefing", w.pages.currentIndex(), 1)
check("toggle offers the way back", w.briefing_toggle.text(), "Chat")
w.toggle_briefing(); [app.processEvents() for _ in range(2)]
check("toggles to chat", w.pages.currentIndex(), 0)
w.toggle_briefing(); [app.processEvents() for _ in range(2)]
check("and back again", w.pages.currentIndex(), 1)

cards = [w.briefing_column.itemAt(i).widget() for i in range(w.briefing_column.count())]
titles = [c for c in cards if c is not None and c.objectName() == "briefCard"]
check("a card per item", len(titles), 2)
check("status line counts them", "2 item(s)" in w.briefing_status.text(), True)
check("interests prefilled", "Godot engine" in w.interests_input.text(), True)

print("\n-- titles are escaped, not interpreted --")
card = w.briefing_card({"title": "Sink & <script>alert(1)</script>", "url":
                        'https://x.test/?a=1&b="2"', "source": "s", "published": "",
                        "summary": "5 < 6 & 7 > 3", "kind": "news"})
label = card.findChild(ga.BodyLabel)
check("markup escaped in the title", "&lt;script&gt;" in label.text(), True)
check("url quoted safely", "&quot;" in label.text() or "%22" in label.text(), True)

print("\n-- editing the topics saves them --")
w.interests_input.setText("Rust gamedev, ECS architecture")
w.save_interests_from_input()
check("saved", ga.load_interests(), ["Rust gamedev", "ECS architecture"])

print("\n-- non-Latin scripts are dropped --")
for title in [
    "7 день делаю свою игру в Godot Engine, имея 0 скилла",
    "ازاي تعمل لعبة في اقل من 30 ثانية #godot",
    "Godot Engineのアーキテクチャ、設計思想を読む配信",
    "고닷 엔진으로 게임 만들기 강좌",
]:
    check(f"dropped: {title[:34]}", ga.looks_english(title), False)

print("\n-- Latin-script foreign languages too --")
for title in [
    "Día 25 de mi juego de terror, sesión más corta pero con mejora",
    "encore un peu rigide mais ça avance dans le jeu",
    "Wie du mit der Godot Engine ein Spiel machst, das nicht schlecht ist",
    "Como fazer um jogo com Godot agora, voce nao precisa de mais nada",
    "Il mio gioco fatto che sono molto contento",
]:
    check(f"dropped: {title[:34]}", ga.looks_english(title), False)

print("\n-- English is kept, including the awkward cases --")
for title in [
    "How do I build a factory in Godot",
    "A la carte rendering: per-object materials in Godot",
    "De-facto standards for ECS design",
    "Godot 4.5 released with new rendering pipeline",
    "I Need More Physics-Based Factory Builders! - Sandustry",
    "No more 'AI slop': Godot's new rules for contributors",
]:
    check(f"kept: {title[:38]}", ga.looks_english(title), True)

print("\n-- too short to judge is kept, not guessed at --")
for title in ["Sandustry", "Godot 4.5", "Factorio 2.0 trailer"]:
    check(f"kept: {title}", ga.looks_english(title), True)

print("\n-- one foreign-looking word is not enough --")
check("'la' alone does not condemn a title",
      ga.looks_english("Rendering a la carte in the Godot engine today"), True)
check("but several do",
      ga.looks_english("Como hacer una cosa con Godot para que sea mas rapido"), False)

print("\n-- the filter can be switched off --")
russian = {"title": "7 день делаю свою игру", "url": "https://x.test/1", "content": "",
           "engine": "bing", "publishedDate": None}
import json as _json

class _Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self): return {"results": [russian, {"title": "Godot 4.5 released today",
                                                  "url": "https://x.test/2", "content": "",
                                                  "engine": "bing"}]}

_real_get = ga.requests.get
ga.requests.get = lambda *a, **k: _Resp()
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_english_only": True})
kept = ga._briefing_query("godot", "news", 5, "month")
check("English only by default", [i["title"] for i in kept], ["Godot 4.5 released today"])
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_english_only": False})
kept = ga._briefing_query("godot", "news", 5, "month")
check("switched off, everything comes through", len(kept), 2)
ga.requests.get = _real_get
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})

print("\n-- diacritics are the signal the dictionary could not be --")
check("three accents is not English", ga.accent_count("Día sesión más") >= 3, True)
check("one loanword accent is fine", ga.looks_english("Beyoncé announces a world tour"), True)
check("two is still fine", ga.looks_english("Pokémon and café culture in Tokyo"), True)
check("the dictionary is deliberately unused",
      "_spell.known" in APP.read_text().split("def looks_english")[1]
      .split("def ")[0], False)

print("\n-- the titles are actually clickable --")
card = w.briefing_card({"title": "Godot 4.5 released", "url": "https://example.org/a",
                        "source": "bing", "published": "", "summary": "", "kind": "news"})
label = card.findChild(ga.BodyLabel)
flags = label.textInteractionFlags()
check("links are accepted by the mouse",
      bool(flags & ga.Qt.TextInteractionFlag.LinksAccessibleByMouse), True)
check("text is still selectable",
      bool(flags & ga.Qt.TextInteractionFlag.TextSelectableByMouse), True)
check("cursor signals it is a link",
      label.cursor().shape(), ga.Qt.CursorShape.PointingHandCursor)

opened = []
w.open_link = lambda url: opened.append(url)
card2 = w.briefing_card({"title": "Another one", "url": "https://example.org/b",
                         "source": "s", "published": "", "summary": "", "kind": "news"})
card2.findChild(ga.BodyLabel).linkActivated.emit("https://example.org/b")
check("activating a link reaches the handler", opened, ["https://example.org/b"])

print("\n-- the ones that actually got through, from the real briefing --")
REAL_FOREIGN = [
    ('Linux CachyOS: "Cachy Hello"-Funktion optimiert das Speed-Linux',
     'Die beliebte Linux-Distribution "CachyOS" bringt mit "Cachy Hello" ein Tool mit'),
    ("COSMIC 1.8.0: System76 bringt Desktop-Umgebung in Rekordtempo voran",
     "COSMIC 1.8.0 bringt System76s Desktop-Umgebung auf Pop!_OS 24.04, verbessert"),
    ("radiolinux 15 agosto",
     "Radiolinux non si ferma nemmeno a ferragosto proponendo delle novita delle del"),
    ("Jak skonfigurowac DaVinci Resolve do pracy pod Steam OS (Arch Linux)? Krok po",
     "I juz po strachu. Wystarczy teraz kliknac w ikonke na pulpicie, by DaVinci"),
    ("Shelly 3.1.3: Arch Linux erhaelt neuen Paketmanager mit AUR-Cleanup",
     "Shelly 3.1.3 erleichtert die Paketverwaltung unter Arch Linux, verbessert"),
    ("La forma correcta de instalar Counter Strike 1.6 en Linux ( Arch Linux ) - 2026",
     "gold client: goldclient.ru wine: Instalar wine y lutris: sudo pacman"),
]
for title, summary in REAL_FOREIGN:
    check(f"dropped: {title[:40]}", ga.looks_english(title, summary), False)

print("\n-- the English ones from the same briefing survive --")
REAL_ENGLISH = [
    ("CachyOS is closing the one gap that used to keep me on Windows",
     "It might finally be the time to say goodbye to the blue squares."),
    ("4 Linux distros I'd trust with my gaming PC before SteamOS",
     "Bazzite is one of the most-recommended Linux distros for gaming."),
    ("I ran Arch Linux for 5 years - Fedora does the same job with less work",
     "Fedora feels like the finished product of an experiment that is Arch Linux."),
    ("The Arch Linux Malware Is Getting Creative",
     "741 views - 6 days ago - YouTube - Brodie Robertson"),
    ("Anthropic CEO Dario Amodei says AI industry needs to give safety measures time",
     "The CEO of Anthropic said the artificial-intelligence industry should slow."),
    ("Stop Thermal Throttling on CachyOS Linux! (Full CoolerControl Setup)",
     "121 views - 3 weeks ago - YouTube - The Indian Pengu"),
]
for title, summary in REAL_ENGLISH:
    check(f"kept: {title[:40]}", ga.looks_english(title, summary), True)

print("\n-- a short title is judged by its summary --")
check("English title, foreign summary is dropped",
      ga.looks_english("radiolinux 15 agosto", "non si ferma nemmeno delle novita"), False)
check("short title with no summary is still kept",
      ga.looks_english("radiolinux 15", ""), True)

print("\n-- a stale cache is filtered on the way out too --")
# An earlier check saved interests, which starts a real background fetch. Let it
# finish before asserting on the status line, or its completion overwrites the text.
if w.briefing_worker is not None:
    w.briefing_worker.cancel()
    w.briefing_worker.wait(10000)
    # wait() returns when the thread ends, but its done signal is queued to this
    # thread - drain it now, or it re-renders over the cache written below.
    for _ in range(6):
        app.processEvents()
w.briefing_worker = None
ga.save_json(ga.BRIEFING_FILE, {"fetched": ga.datetime.now().isoformat(timespec="minutes"),
    "topics": [{"topic": "CachyOS", "items": [
        {"title": REAL_FOREIGN[0][0], "summary": REAL_FOREIGN[0][1], "url": "https://x/1",
         "source": "s", "published": "", "kind": "news"},
        {"title": REAL_ENGLISH[0][0], "summary": REAL_ENGLISH[0][1], "url": "https://x/2",
         "source": "s", "published": "", "kind": "news"}]}]})
w.render_briefing()
[app.processEvents() for _ in range(2)]
cards = [w.briefing_column.itemAt(i).widget() for i in range(w.briefing_column.count())]
shown = [c for c in cards if c is not None and c.objectName() == "briefCard"]
check("only the English one is drawn", len(shown), 1)
check("status counts the filtered total", "1 item(s)" in w.briefing_status.text(), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
