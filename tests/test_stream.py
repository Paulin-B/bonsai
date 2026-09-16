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

box = Path(tempfile.mkdtemp(prefix="bonsai-stream-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE",
          "INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.CHATS_DIR = box / "chats"
app = ga.QApplication(sys.argv)

class FakeStream:
    """A server-sent reply shaped like the real one: reasoning first, content after."""
    status_code = 200
    def __init__(self, reasoning, content):
        self.lines = ([b'data: {"choices":[{"delta":{"role":"assistant","content":null}}]}']
                      + [b'data: {"choices":[{"delta":{"reasoning_content":"%s"}}]}' % r.encode()
                         for r in reasoning]
                      + [b'data: {"choices":[{"delta":{"content":"%s"}}]}' % c.encode()
                         for c in content]
                      + [b"", b"data: [DONE]"])
    def iter_lines(self): return iter(self.lines)
    def __enter__(self): return self
    def __exit__(self, *a): return False

def worker_with(reasoning, content):
    w = ga.Worker("hi", False, [], {"max_tool_steps": 3, "native_tools": False,
                                    "stream_replies": True})
    ga.requests.post = lambda *a, **k: FakeStream(reasoning, content)
    return w

print("\n-- reasoning is counted, never shown --")
real_post = ga.requests.post
w = worker_with(["The", " user", " wants"] * 4, ["One", ", two", ", three."])
pieces, thought = [], []
w.chunk.connect(pieces.append)
w.thinking.connect(thought.append)
text = w.stream_reply({}, "http://x", 10)
ga.requests.post = real_post
check("only content is emitted", "".join(pieces), "One, two, three.")
check("and returned whole", text, "One, two, three.")
check("no reasoning leaked into the reply", "user wants" in text, False)
check("thinking was reported", thought, [8])

print("\n-- cancelling stops mid-stream --")
w = worker_with([], ["a", "b", "c", "d"])
w._cancelled = True
text = w.stream_reply({}, "http://x", 10)
ga.requests.post = real_post
check("nothing collected after cancel", text, "")

print("\n-- a server error is raised, not silently empty --")
class Failing(FakeStream):
    status_code = 500
    text = "boom"
ga.requests.post = lambda *a, **k: Failing([], [])
w = ga.Worker("hi", False, [], {})
try:
    w.stream_reply({}, "http://x", 10)
    raised = False
except RuntimeError as exc:
    raised = "500" in str(exc)
ga.requests.post = real_post
check("raises on a bad status", raised, True)

print("\n-- only the closing call streams --")
text = APP.read_text()
check("wrap-up streams", text.count("with_tools=False, stream=True)[0])"), 2)
check("tool-selection steps do not",
      "temperature=tool_temp, stream=True" in text, False)

print("\n-- the view repaints on a timer, not per token --")
check("throttled", "self.stream_timer.start(120)" in text, True)
# Order matters, adjacency does not - assert the former.
_body = text.split("def on_reply(")[1].split("\n    def ")[0]
check("the live view is dropped before the final reply is added",
      _body.index("self.end_stream()") < _body.index("add_assistant(reply)"), True)
check("the elapsed-time counter is stopped too", "self.clear_busy()" in _body, True)

print("\n-- the widget can be updated in place --")
view = ga.MarkdownView("", ga.THEMES["dark"])
view.set_markdown("# Title\n\nsome **bold** text")
check("renders", "bold" in view.toPlainText(), True)
view.set_markdown("# Title\n\nsome **bold** text and more")
check("re-renders", "and more" in view.toPlainText(), True)
check("previous content replaced, not appended",
      view.toPlainText().count("Title"), 1)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
