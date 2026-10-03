import sys
import tempfile
from pathlib import Path
from bonsai_under_test import load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

from bonsai.text import split_reasoning, ThinkSplitter, denoise, extract_tool_call
from bonsai.worker import Worker

print("-- inline thinking is separated from the answer --")
check("closed block", split_reasoning("<think>hm</think>Hello"), ("hm", "Hello"))
check("no thinking is untouched", split_reasoning("Hello"), ("", "Hello"))
check("cut off while thinking leaves no answer", split_reasoning("<think>still going"), ("still going", ""))
check("template opened the block", split_reasoning("hm</think>Hello"), ("hm", "Hello"))
check("denoise drops the trace", denoise("<think>x</think>Hi").strip(), "Hi")

print("\n-- a trace mentioning a tool does not run it --")
trace = "<think>I could [TOOL: READ_FILE | /etc/passwd] but no.</think>Done."
check("no call extracted", extract_tool_call(trace), (None, None))

print("\n-- the stream splitter survives awkward chunk boundaries --")
def run(pieces):
    s, a, t = ThinkSplitter(), "", ""
    for p in pieces:
        x, y = s.feed(p); a += x; t += y
    return a + s.flush(), t
check("tag split across chunks", run(["<thi", "nk>plan", "</th", "ink>Ans", "wer"]), ("Answer", "plan"))
check("plain text with a '<'", run(["1 < 2 and ", "3 <4"]), ("1 < 2 and 3 <4", ""))
check("unclosed thought never becomes the answer", run(["<think>abc"]), ("", "abc"))

print("\n-- the worker reads every server dialect --")
class FakeStream:
    status_code = 200
    def __init__(self, deltas):
        import json
        self.lines = [("data: " + json.dumps({"choices": [{"delta": d}]})).encode() for d in deltas] + [b"data: [DONE]"]
    def iter_lines(self): return iter(self.lines)
    def __enter__(self): return self
    def __exit__(self, *a): return False
def stream(deltas):
    w = Worker("hi", False, [], {})
    ga.requests.post = lambda *a, **k: FakeStream(deltas)
    seen = []
    w.chunk.connect(seen.append)
    return w, w.stream_reply({}, "http://x", 10), seen
w, text, seen = stream([{"reasoning": "think"}, {"content": "Hi"}])
check("`reasoning` field", (text, w.last_reasoning), ("Hi", "think"))
w, text, seen = stream([{"content": "<think>a"}, {"content": "b</think>Hi"}])
check("inline tags", (text, w.last_reasoning, "".join(seen)), ("Hi", "ab", "Hi"))

print("\n-- Gemma 4 channel markers, as they appear in a real debug log --")
real = '<|channel>thought\n<channel|><|channel>thought\n<channel|><channel|><|tool_call>call:TOOL:LIST_DIR: /x<tool_call|>'
check("empty thought blocks vanish", "channel" in denoise(real), False)
check("the call underneath survives", extract_tool_call(real), ("LIST_DIR", "/x"))
check("a thought with content is split off",
      split_reasoning("<|channel>thought\nplan it<channel|>Hi"), ("plan it", "Hi"))
loop = "<|channel>thought\n<channel|>" * 500
check("a 500-deep loop of empty thoughts is an empty reply", denoise(loop).strip(), "")
check("streamed in awkward pieces", run(["<|chan", "nel>thou", "ght\nabc<chan", "nel|>Hi"]), ("Hi", "abc"))

print("\n-- a vanished file does not crash a listing --")
import os, tempfile
from bonsai.files import list_directory
d = Path(tempfile.mkdtemp(prefix="bonsai-ls-"))
(d / "sub").mkdir(); (d / "sub" / "real.txt").write_text("hi")
os.symlink("/nonexistent/target", d / "sub" / "SingletonCookie")
os.symlink("/nonexistent/target", d / "dangling")
out = list_directory(str(d))
check("listing completes", "real.txt (2 bytes)" in out, True)
check("dangling links are named, not fatal", out.count("broken link"), 2)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
