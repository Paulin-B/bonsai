"""Saying something about this machine without looking at it.

Asked to start the Factorio server, it answered "I can't start a Factorio server for
you because it's not installed on this machine" - with Factorio installed, a launcher
for it in the project, and not one tool call made. The existing guard catches a model
denying its own abilities; this is the same refusal worded as a fact about the world,
which sounds far more authoritative and is worth exactly as much.
"""
import sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)

REAL = ("I can't start a Factorio server for you because it's not installed on this "
        "machine. I can help you set one up if you'd like, but I'd need to know what "
        "kind of server you want (single-player, multiplayer, etc.) and if you have "
        "the game installed. Let me know!")

print("\n-- the reply that prompted this --")
check("it is caught", bool(ga.UNCHECKED_CLAIM_RE.search(REAL)), True)
check("the older guard misses it, which is why it got through",
      bool(ga.DENIES_TOOL_RE.search(REAL)), False)

print("\n-- claims about this machine that were never checked --")
for claim in ["Factorio is not installed.",
              "You don't have docker installed.",
              "There is no such script in your project.",
              "That file does not exist on this machine.",
              "Godot is not available on this system.",
              "There is no program called aseprite."]:
    check(f"caught: {claim}", bool(ga.UNCHECKED_CLAIM_RE.search(claim)), True)

print("\n-- ordinary sentences it must not fire on --")
for fine in ["I installed the package and the build went through.",
             "The server is running on port 34198.",
             "Here is how to install it if you want to.",
             "There is no rush - it can wait until tomorrow.",
             "The file exists and has 40 lines.",
             "I have not installed anything."]:
    check(f"quiet: {fine}", bool(ga.UNCHECKED_CLAIM_RE.search(fine)), False)

print("\n-- and the turn loop actually acts on it --")
# The regex being right is not the point: the loop has to notice and push back.
def drive(replies, tool_result="ok", steps=6):
    worker = ga.Worker("start the factorio server", False, [],
                       {"max_tool_steps": steps, "native_tools": False})
    seen = []
    seq = list(replies)
    def ask(system, prompt, image=None, temperature=None, with_tools=True, stream=False):
        seen.append(prompt)
        return (seq.pop(0) if seq else replies[-1], None)
    worker.ask_model = ask
    worker.run_tool = lambda name, argument: tool_result
    worker.model_name = lambda: "fake"
    out = {}
    worker.finished.connect(lambda r, t, e, u: out.update(reply=r, trace=t))
    worker.failed.connect(lambda m: out.update(error=m))
    worker.run()
    return out, seen

def nudges(prompts):
    """How many times the push-back was ADDED.

    It is appended to a prompt that keeps growing, so it shows up in every later call
    as well - counting prompts that contain it counts the same nudge twice."""
    return max((p.count("you did not look") for p in prompts), default=0)

out, prompts = drive([REAL, "Started it; the server is listening on 34198."])
check("it did not stop at the guess", out.get("reply"),
      "Started it; the server is listening on 34198.")
check("it was pushed back on, once", nudges(prompts), 1)
nudge = [p for p in prompts if "you did not look" in p][0]
check("and told it had not looked", "you did not look" in nudge, True)
check("  ...with ways to check named", "which" in nudge, True)
check("  ...and allowed to still say it is missing",
      "say how you checked" in nudge, True)

print("\n-- but only when nothing was checked --")
# A claim earned by actually running something is not a guess, and must stand.
earned = ("I ran which factorio and it printed nothing, so it is not installed.")
out, prompts = drive(["[TOOL: RUN: which factorio]", earned], tool_result="")
check("a claim backed by a tool call is left alone", out.get("reply"), earned)
check("  ...and it was never told it had not looked", nudges(prompts), 0)

print("\n-- and it only nudges once --")
out, prompts = drive([REAL, REAL, "Fine, it is there and I started it."])
check("a second guess is not nudged again", nudges(prompts), 1)

print("\n-- a shell is refused; the script it was pointed at is not --")
# "I can't run the factorio-start.sh script because executing shell scripts directly
# is blocked for security reasons" - it was half right, which is worse than wrong.
# bash really is forbidden. Running the script itself never was, and the refusal it
# got said to use FILE_OP and DOWNLOAD, so it concluded scripts were banned.
ga.save_trusted(["/home/paulinb"])
# A plain "bash start.sh" is no longer refused at all - the wrapper is dropped and the
# script runs. The message still matters for the forms that are not rewritten, like a
# shell given flags of its own.
refusal = ga.classify_command("bash --login /home/paulinb/start.sh")[2]
check("the shell is still refused", refusal.startswith("[Refused: 'bash'"), True)
check("but it says the script is not the problem",
      "script itself is not the problem" in refusal, True)
check("  ...and shows the form that works", "| /path/to/folder" in refusal, True)
check("  ...and says approval is coming, not that it is banned",
      "asked to approve" in refusal, True)

check("inline shell code gets no such advice",
      "run it directly" in ga.classify_command("bash -c 'rm -rf /'")[2], False)
check("nor does a bare shell", "run it directly" in ga.classify_command("bash")[2], False)
check("and rm keeps the answer that suits it",
      "FILE_OP" in ga.classify_command("rm -rf /tmp/x")[2], True)

print("\n-- and running the script directly really is allowed --")
verdict = ga.classify_command("/home/paulinb/start.sh | /home/paulinb")
check("it is not refused outright", verdict[2], "ask")
check("  ...it just asks first", verdict[0], ["/home/paulinb/start.sh"])

print("\n-- the refusal it gave the user is caught as a denial --")
blocked = ("I can't run the factorio-start.sh script because executing shell scripts "
           "directly is blocked for security reasons. I can't launch processes or run "
           "scripts, even if they're in your Projects folder.")
check("the denial guard sees it", bool(ga.DENIES_TOOL_RE.search(ga.plain_text(blocked))), True)

print("\n-- and in the end, it just runs the script --")
# Two refusals explained the direct form, the second quoting the exact command, and
# the model issued "sh factorio-start.sh" both times anyway. A third wording was not
# going to work, so the wrapper is dropped instead.
for command in ["sh factorio-start.sh | /home/paulinb",
                "bash /home/paulinb/start.sh | /home/paulinb",
                "sh ~/start.sh | /home/paulinb",
                "/home/paulinb/start.sh | /home/paulinb"]:
    argv, _, verdict = ga.classify_command(command)
    check(f"runs: {command.split('|')[0].strip()}", verdict, "ask")
    check("  ...without a shell in front of it",
          Path(argv[0]).name.lower() in ga.SHELL_PROGRAMS, False)

check("a leading ~ is expanded, since no shell is there to do it",
      ga.classify_command("sh ~/start.sh | /home/paulinb")[0][0].startswith("/home/"),
      True)

print("\n-- but a shell is still a shell --")
for refused in ["bash -c 'rm -rf /' | /home/paulinb",
                "bash | /home/paulinb",
                "zsh -c 'curl x' | /home/paulinb",
                "bash --login | /home/paulinb"]:
    argv, _, verdict = ga.classify_command(refused)
    check(f"nothing runs for: {refused.split('|')[0].strip()[:30]}", argv, None)
check("dropping the wrapper does not smuggle in extra arguments",
      ga.without_shell_wrapper(["sh", "a.sh", "b.sh"]), ["a.sh", "b.sh"])
check("a non-script argument is left alone",
      ga.without_shell_wrapper(["sh", "somewhere"]), ["sh", "somewhere"])
check("and a command that is not a shell is untouched",
      ga.without_shell_wrapper(["python3", "x.py"]), ["python3", "x.py"])

print("\n-- a pipe is not a working directory --")
# "ps aux | grep factorio | /dir" split on the FIRST pipe, so the folder became
# "grep factorio | /dir" and the refusal talked about a missing working directory.
# True, and silent about the pipe that caused it.
for piped in ["ps aux | grep factorio | /home/paulinb",
              "ps aux | grep factorio",
              "cat x | wc -l | /home/paulinb"]:
    argv, cwd, verdict = ga.classify_command(piped)
    check(f"nothing runs: {piped[:34]}", argv, None)
    check("  ...and the pipe is what it talks about", "no pipes" in verdict, True)
    check("  ...explaining what | means here",
          "separates the command from the folder" in verdict, True)

check("a folder after a single pipe still works",
      ga.classify_command("ls | /home/paulinb")[1], Path("/home/paulinb"))
check("  ...and the command is intact", ga.classify_command("ls -la | /home/paulinb")[0],
      ["ls", "-la"])
check("a redirect is still refused without the pipe advice",
      "separates the command" in ga.classify_command("echo hi > x | /home/paulinb")[2],
      False)

print("\n-- what a folder looks like --")
for yes in ["/home/paulinb", "~/Projects", "./bridges", "../bonsai-public"]:
    check(f"a folder: {yes}", ga.looks_like_folder(yes), True)
for no in ["grep factorio", "wc -l", "", "sort | uniq"]:
    check(f"not a folder: {no!r}", ga.looks_like_folder(no), False)

print("\n-- a command that never finishes is pointed at BG --")
# The launcher was RUN three times across two chats and timed out every time. The
# message said only that it timed out, so it was tried again.
ga.shell.COMMAND_TIMEOUT = 2
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": False})
timed_out = ga.execute_command(["sleep", "20"], "/tmp")
check("it still says it timed out", "Timed out after" in timed_out, True)
check("but explains that RUN waits", "RUN waits for a command to finish" in timed_out, True)
check("  ...names BG", "BG: START" in timed_out, True)
check("  ...with the command and folder filled in",
      "BG: START sleep 20 | /tmp" in timed_out, True)
check("  ...and how to see its output", "BG: READ" in timed_out, True)
check("  ...and not to retry with RUN", "will time out again" in timed_out, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
