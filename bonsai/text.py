"""Parsing what the model says, and noticing when it says something untrue."""

import re
from pathlib import Path
from .config import (
    CHARACTER_FILE, MEMORY_FILE,
)
from .store import (
    all_skills, load_character, load_memory, record_project_note, save_json, settings,
)


TOOL_NAMES = ("SEARCH|FETCH|READ_FILE|LIST_DIR|FILE_OP|SAVE_SKILL|USE_SKILL|RECALL"
               "|DOWNLOAD|RUN|TASK|FIND|HISTORY|SAVE_NOTE|SEARCH_VAULT"
               "|SEARCH_IMAGES|MAKE_PDF|MAKE_CHART|LAUNCH|LOOK|PREVIEW|USAGES")


# Models routinely emit [TOOL: WRITE | ...] instead of [TOOL: FILE_OP: WRITE | ...].
# Accept the sub-action as a top-level name and normalise it back to FILE_OP.
FILE_OP_ACTIONS = "RENAME|MOVE|COPY|DELETE|MKDIR|WRITE|EDIT"


ALL_TOOL_NAMES = f"{TOOL_NAMES}|{FILE_OP_ACTIONS}"


NOISE_RE = re.compile(
    r"<\|?/?tool_call\|?>|<\|?/?think(ing)?\|?>|</?think(ing)?>"
    r"|\[(?:Start|End) thinking\]|<unused\d+>|<\|\"\|>",
    re.IGNORECASE,
)


TOOL_BRACKETED_RE = re.compile(rf"\[(?:TOOL:\s*)?({ALL_TOOL_NAMES})\s*[|:]\s*(.+?)\]", re.I | re.S)


TOOL_LINE_RE = re.compile(
    rf"^[ \t>*\-\[]*(?:call:\s*|tool:\s*)?({ALL_TOOL_NAMES})\s*[|:][ \t]*(\S.*)$", re.I | re.M)


TOOL_LOOSE_RE = re.compile(rf"(?:call|tool)\s*:\s*({ALL_TOOL_NAMES})\s*[|:]\s*([^\n\]]+)", re.I)


# Last resort: a bracketed tag naming something that is not a real tool. Restricted
# to the bracketed form so ordinary prose cannot trip it.
UNKNOWN_TOOL_RE = re.compile(r"\[(?:TOOL|CALL):\s*([A-Za-z][\w-]{2,30})\s*[|:]\s*(.+?)\]", re.S)


# JSON-ish form: READ_FILE{path: "/x"} or FILE_OP{action:"WRITE", path:"a", content:"b"}.
BRACE_TOOL_RE = re.compile(
    rf"(?:\[?\s*(?:call|tool)\s*:\s*)?(?:TOOL:\s*)?(?:FILE_OP:\s*)?({ALL_TOOL_NAMES})"
    rf"\s*\{{(.*?)\}}", re.I | re.S)


QUOTED_RE = re.compile(r'"([^"]*)"' + r"|'([^']*)'")


def _args_from_braces(inner):
    """Take the quoted values in order and join them with the pipe separator the
    tools already expect, so {action:"WRITE", path:"a", content:"b"} becomes
    'WRITE | a | b'."""
    values = [a or b for a, b in QUOTED_RE.findall(inner)]
    if values:
        return " | ".join(v.strip() for v in values if v.strip())
    return inner.strip().strip("{}").strip()


# Written file content (Python lists, JSON, markdown) frequently contains ']', which
# terminates the non-greedy pattern early and silently truncates the file. For writes
# we re-read the argument greedily instead. EDIT carries code for the same reason.
TOOL_GREEDY_RE = re.compile(
    rf"\[?(?:TOOL:\s*)?(?:FILE_OP:\s*)?\b(WRITE|EDIT)\b\s*[|:]\s*(.+)", re.I | re.S)


# Marks where a following tool call begins, so a WRITE payload stops there.
NEXT_TOOL_RE = re.compile(
    # A following call starts either with a bracket, or with an explicit call:/tool:
    # prefix. Requiring one of those keeps ordinary prose in written content from
    # being mistaken for the start of a new tool call.
    rf"\n\s*(?:\[|(?:call|tool)\s*:\s*)(?:TOOL:\s*)?(?:FILE_OP:\s*)?"
    rf"({ALL_TOOL_NAMES})\s*[|:]", re.I)


REMEMBER_RE = re.compile(r"^[ \t>*\-\[]*REMEMBER:\s*(\S.*?)\]?\s*$", re.I | re.M)


PROJECT_NOTE_RE = re.compile(r"^[ \t>*\-\[]*PROJECT:\s*(\S.*?)\]?\s*$", re.I | re.M)


EVOLVE_RE = re.compile(
    r"^[ \t>*\-\[]*EVOLVE:\s*(TRAIT|OPINION|JOKE):\s*(\S.*?)\]?\s*$", re.I | re.M)


# The record is stored with this exact opening, so both the app that writes it and the
# strippers that recognise it copied back agree on one spelling.
RECORD_HEADER = "[SYSTEM RECORD - what the app actually did this turn:"


ACTIONS_ECHO_RE = re.compile(
    r"(\(Actions I performed this turn:.*?(?:\)|\Z)|\[SYSTEM RECORD.*?(?:\]|\Z)"
    r"|^.*?Anything not listed here did NOT happen\.\])\s*", re.S | re.I)


EVOLVE_KEYS = {"TRAIT": "learned_traits", "OPINION": "opinions", "JOKE": "running_jokes"}


# Phrases that assert an action was taken. If one appears in a turn where no tool
# actually ran, the claim is unverifiable - surface that rather than let it pass.
CLAIMED_ACTION_RE = re.compile(
    r"\b(?:I(?:'ve| have)?\s+"
    r"(?:now\s+|just\s+|already\s+|finished\s+|successfully\s+|gone ahead and\s+){0,3}"
    r"(?:updated|replaced|written|wrote|created|saved|overwrote|overwritten|deleted|"
    r"renamed|moved|downloaded|installed|fixed|swapped|updating|writing|replacing|"
    r"implemented|completed|finished|built|added|established|refactored|generated|"
    r"set up|put together|removed|cleared|closed|marked)"
    r"|I(?:'m| am)\s+(?:now\s+)?(?:drafting|writing|creating|saving|adding)\s+(?:this|that|it|a|an|the)"
    r"|consider it done|done!|it'?s done|all set|successfully (?:wrote|created|updated|"
    r"replaced|downloaded|renamed)|(?:are|is) now (?:updated|saved|written|correctly saved))",
    re.IGNORECASE,
)


# Tools whose whole purpose is to change something. A bracketed result from one of
# these means the change did NOT happen - unlike a read-only tool, where "[No search
# results found.]" is a perfectly good answer rather than a failure.
MUTATING_TOOLS = {"FILE_OP", "DOWNLOAD", "RUN"}


# A call repeated this many times is not run again: the answer cannot change, and
# warning about it does not stop a model that has started looping.
MAX_IDENTICAL_CALLS = 2


# Refusals in a row that mean the turn is going nowhere and should simply end.
MAX_CONSECUTIVE_REFUSALS = 5


# The turn record is stored as "- ran TOOL and got: ..." lines, and a looping model
# copies that shape back out as if it were its own prose.
RECORD_ECHO_RE = re.compile(
    r"^[ \t>*\-]*ran\s+[A-Z_]{3,12}\s+(?:\d+\s+times[^:]*|and got):.*$", re.M | re.I)


REDACTED = "[REDACTED-BY-BONSAI]"


# Key names whose value is a credential. Matched as a whole word or a suffix, so
# "db_password" and "PASSWORD" both hit while "password_hint" and "token_count" - a
# count, not a token - do not.
SECRET_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:[A-Za-z0-9_.-]*?_)?"
    r"(?:passwd|password|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|credentials?|auth[_-]?token|session[_-]?key|bearer)"
    r"(?![A-Za-z0-9_])", re.I)


# "KEY = value", "KEY: value", "KEY=value" - the assignment forms an .env, a .tf, a
# YAML or a TOML file all use.
ASSIGNMENT_RE = re.compile(
    r"^(?P<lead>[ \t\-]*\"?)(?P<key>[A-Za-z0-9_.\[\]-]+)(?P<mid>\"?[ \t]*[:=][ \t]*)"
    r"(?P<value>.+?)(?P<trail>[ \t]*,?[ \t]*)$", re.M)


# A value that carries no secret however it is named.
HARMLESS_VALUE_RE = re.compile(
    r"^(?:\"\"|\'\'|''|true|false|null|none|nil|\d+(?:\.\d+)?|"
    r"[\"\']?(?:x{3,}|\*{3,}|<[^>]*>|\$\{[^}]*\}|\$[A-Z_]+|changeme|your[-_].*|"
    r"todo|tbd|replace[-_]?me|placeholder)[\"\']?)$", re.I)


PEM_RE = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)",
                    re.S)


# Credentials inside a connection string: scheme://user:password@host
URL_CREDENTIAL_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)", re.I)


# Token shapes that are unmistakable whatever they are called or wherever they appear.
TOKEN_SHAPE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[A-Za-z0-9_-]{30,})")


def redact_secrets(text):
    """Blank out credentials in file content before the model ever sees them.

    Reading a file is how a password in a .env, a .tf or a config reaches the model,
    and from there the server - which is not always the one on this machine, since an
    endpoint can be a remote API. Paths that are obviously credential stores are already
    refused; this is for the secret sitting in an ordinary file in a folder you trust.

    The key is always kept and only the value is replaced, so the model can still see
    that the setting exists and reason about it. Returns the text and how many values
    went, because a redaction nobody is told about is its own kind of lie."""
    if not text:
        return text, 0
    count = 0

    def blank(match):
        nonlocal count
        count += 1
        return REDACTED

    text, n = PEM_RE.subn(lambda m: m.group(1) + "\n" + REDACTED + "\n" + m.group(3), text)
    count += n
    text, n = URL_CREDENTIAL_RE.subn(lambda m: m.group(1) + REDACTED + m.group(3), text)
    count += n
    text, n = TOKEN_SHAPE_RE.subn(blank, text)

    def assignment(match):
        nonlocal count
        value = match.group("value").strip()
        if not SECRET_KEY_RE.search(match.group("key")):
            return match.group(0)
        if not value or HARMLESS_VALUE_RE.match(value) or REDACTED in value:
            return match.group(0)
        count += 1
        return (match.group("lead") + match.group("key") + match.group("mid")
                + REDACTED + match.group("trail"))

    return ASSIGNMENT_RE.sub(assignment, text), count


def strip_record_echo(reply):
    """Remove stray record lines from a reply - but hand back a reply that is ONLY the
    record untouched.

    Tidying away the odd copied line is right. Tidying away a whole turn is not: the
    strip left a stub that read like a terse answer, so nothing downstream could tell
    that the turn had done nothing at all, and auto mode moved on to the next round."""
    if is_record_echo(reply):
        return reply
    return RECORD_ECHO_RE.sub("", ACTIONS_ECHO_RE.sub("", reply).strip()).strip()


def blocked_as_repeat(name, repeats, last_failed):
    """Whether a tool call should be refused for being an exact repeat.

    Repeating a call that already answered is how a turn loops forever, so it is
    refused. Re-reading a file after a failure is the exception: an EDIT that does not
    match means what was believed about the file is wrong, and looking again is the
    only way to find out how. Blocking that left a real run with nothing to do but
    guess, and it retried the same failing edit until the turn was ended."""
    if repeats < MAX_IDENTICAL_CALLS:
        return False
    return not (name == "READ_FILE" and last_failed)


def narrate_trace(trace):
    """The turn's tool calls as prose, with repeated calls collapsed to one line.

    Two separate lessons are baked in here. Storing the calls as "NAME: arg -> result"
    gave the model a tool-call-shaped template it copied back out as if it were its own;
    prose does not read as a template. And collapsing repeats matters just as much: a
    turn that called the same thing over and over produced one identical line per call,
    which is a page of text carrying a single fact, and a model that then echoes its own
    record has a page to multiply rather than a line."""
    counts, order = {}, []
    for line in (trace or "").splitlines():
        if not line.strip():
            continue
        pair = (line.split(":", 1)[0], line.split(" -> ", 1)[-1])
        if pair not in counts:
            counts[pair] = 0
            order.append(pair)
        counts[pair] += 1
    out = []
    for name, got in order:
        times = counts[(name, got)]
        out.append(f"- ran {name} and got: {got}" if times == 1 else
                   f"- ran {name} {times} times, getting the same answer every time: {got}")
    return "\n".join(out)


def is_record_echo(reply):
    """Whether a reply is the turn record copied back instead of a reply.

    The record exists to stop the model claiming work it did not do. Putting it in the
    assistant's own turn taught it that an assistant turn looks like a record, so it
    started producing one in place of working - hundreds of duplicated "ran X and got"
    lines and no tool calls at all, running to the token limit mid-line. A reply that is
    only the record is not a short reply, it is a turn that did nothing."""
    text = reply or ""
    if not RECORD_ECHO_RE.search(text):
        return False
    left = RECORD_ECHO_RE.sub("", ACTIONS_ECHO_RE.sub("", text))
    left = left.replace(RECORD_HEADER, "")
    left = re.sub(r"Anything not listed[^\n]*", "", left)
    return len(left.strip(" \t\r\n-*>[]")) < 40


# render_results feeds this turn's results to the model as
#   --- READ_FILE (/path) RESULT ---
# so the model has seen thousands of them, and it will write one itself: a header in
# exactly that shape followed by invented file contents. Rendered into the transcript
# it is indistinguishable from the real thing, and it is how three .gd files were
# "read" whose contents existed nowhere on disk.
#
# A reply must never contain one. Real results are injected by the app into the
# prompt; anything in this shape in the reply was typed by the model.
# Just the header line, for checking one line at a time.
RESULT_ECHO_HEADER_RE = re.compile(
    r"^[ \t>*]*-{2,}\s*[A-Z][A-Z_]{2,15}\s*\([^)\n]*\)\s*RESULT\s*-{2,}[ \t]*$")

RESULT_ECHO_RE = re.compile(
    r"^[ \t>*]*-{2,}\s*(?P<tool>[A-Z][A-Z_]{2,15})\s*\((?P<arg>[^)\n]*)\)"
    r"\s*RESULT\s*-{2,}[ \t]*$"
    r"(?P<body>.*?)(?=^[ \t>*]*-{2,}\s*[A-Z][A-Z_]{2,15}\s*\(|\Z)",
    re.M | re.S)


def strip_result_echoes(reply, trace=""):
    """Remove faked tool-result blocks; return (clean reply, invented, echoed).

    A block whose call really did run this turn is a pointless duplicate of something
    the model was already shown. One that did not run is fabricated evidence, and the
    difference is checkable against the trace."""
    invented, echoed = [], []
    for match in RESULT_ECHO_RE.finditer(reply or ""):
        tool, argument = match.group("tool"), match.group("arg").strip()
        ran = re.search(rf"^{re.escape(tool)}: .*{re.escape(argument[:60])}",
                        trace or "", re.M) if argument else None
        (echoed if ran else invented).append(f"{tool} ({argument})")
    return RESULT_ECHO_RE.sub("", reply or "").strip(), invented, echoed


def mutation_target(name, argument):
    """What a mutating call was aimed at, so a later retry can clear its failure."""
    if name == "FILE_OP":
        return split_file_op(argument)[1]
    if name == "DOWNLOAD":
        parts = argument.split("|", 1)
        return parts[1].strip() if len(parts) > 1 else argument.strip()
    return argument.strip()


# Reference to a conversation other than this one. The model has no memory of past
# chats - they reach it only through HISTORY - so an answer like this that was not
# looked up is invention, however confident it sounds.
CLAIMED_RECALL_RE = re.compile(
    r"\b(?:earlier|previous|prior|another|last|past)\s+"
    r"(?:conversation|chat|session|discussion|time|week)\b"
    r"|\bwe (?:previously|earlier|already)\s+(?:decided|discussed|agreed|talked|settled)"
    r"|\blast time we\b",
    re.IGNORECASE)


# ...unless it is saying it cannot remember, which is the honest answer.
DENIES_RECALL_RE = re.compile(
    r"\b(?:don't|do not|cannot|can't|no)\s+(?:have\s+)?(?:any\s+)?"
    r"(?:access|record|memory|recollection|way)\b"
    r"|\bI (?:don't|do not) (?:remember|recall|know)\b"
    r"|\bnot (?:able to|sure)\s+(?:recall|remember)\b",
    re.IGNORECASE)


HISTORY_CALL_RE = re.compile(r"^HISTORY:", re.IGNORECASE | re.M)


# Disclaiming a capability it has. Twice in three runs, asked to check for system
# updates, it answered "I don't have access to your package manager" without calling
# anything - the tools were there and simply went unused.
DENIES_TOOL_RE = re.compile(
    r"\b(?:can(?:no|')t|cannot|unable to|not able to|do(?:n't| not) have (?:the )?"
    r"(?:ability|access|permission)s?)\b[^.!?\n]{0,70}"
    r"\b(?:run|execute|check|access|read|open|list|search|look|see|inspect|query)\b"
    r"|\bI(?:'m| am) (?:just )?(?:an? )?(?:AI|language model|text-based)\b"
    r"|\bdo(?:n't| not) have (?:direct )?access to your\b",
    re.IGNORECASE)


RECALL_CALL_RE = re.compile(r"^RECALL:", re.IGNORECASE | re.M)


# Pleading ignorance about the user specifically - which is only honest once archival
# memory has actually been searched, since core memory is a deliberately small slice of
# what is stored.
DENIES_KNOWING_USER_RE = re.compile(
    r"\b(?:don't|do not|cannot|can't|couldn't|could not)\s+(?:seem to\s+)?"
    r"(?:have|know|find|recall|remember)\b[^.!?\n]{0,60}\byour?\b"
    r"|\bno\s+(?:information|record|details|data|memory|knowledge)\b[^.!?\n]{0,60}\byour?\b"
    r"|\bI(?:'m| am) not sure\b[^.!?\n]{0,60}\byour\b"
    r"|\b(?:don't|do not|cannot|can't)\s+have\s+access\s+to\b[^.!?\n]{0,60}\byour\b"
    r"|\bunless you (?:provide|share|tell|give)\b",
    re.IGNORECASE)


def unsearched_memory(reply, trace):
    """True when it claims not to know something about the user while archival memory
    holds facts it never looked at. Core memory is only the small always-loaded slice;
    everything else is reachable, so 'I don't know' is premature until RECALL has run."""
    reply = plain_text(reply)
    if not DENIES_KNOWING_USER_RE.search(reply):
        return False
    if RECALL_CALL_RE.search(trace or ""):
        return False
    try:
        return bool(load_memory()["archival"])
    except Exception:
        return False


def invented_recall(reply, trace):
    """True when the reply speaks about an earlier conversation it never looked up."""
    reply = plain_text(reply)
    if not CLAIMED_RECALL_RE.search(reply):
        return False
    if DENIES_RECALL_RE.search(reply):
        return False
    return not HISTORY_CALL_RE.search(trace or "")


# Claims that the task list changed, and the trace evidence that it actually did.
# A reply can describe removing seven tasks while only TASK: LIST ever ran.
CLAIMED_TASK_RE = re.compile(
    r"\b(?:tasks?\s*\[?\d+\]?\s*(?:\([^)]*\)\s*)?(?:has been|have been|was|were|is|are)?"
    r"\s*(?:removed|deleted|cleared|closed)"
    r"|(?:removed|deleted|cleared)\s+(?:the\s+|all\s+|any\s+)?(?:completed\s+|done\s+)?tasks?"
    r"|tasks?\s+(?:have been|has been|were|was)\s+(?:removed|deleted|cleared|closed))",
    re.IGNORECASE)


TASK_MUTATION_RE = re.compile(r"^TASK:\s*(ADD|DONE|REMOVE|CLEAR)", re.IGNORECASE | re.M)


# Filenames mentioned in a reply, used to spot files claimed but never touched.
FILENAME_RE = re.compile(
    r"\b([\w.-]+\.(?:gd|py|js|ts|json|md|txt|yml|yaml|sh|fish|c|cpp|h|rs|go|qml|html|css|zip))\b")


def awaits_an_answer(text, window=6):
    """True when the reply's closing lines put a question to the user.

    The question is often not the last line - it tends to be followed by the numbered
    options it wants picked from - so the whole closing passage is checked, not just
    the final sentence. Unattended, any of these is a round spent waiting for an answer
    nobody is going to give."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return any(line.rstrip("*_`> ").endswith("?") for line in lines[-window:])


def render_results(entries, budget):
    """Lay out this turn's tool results within a character budget.

    Recent results stay whole, because that is what the model is working from. Older
    ones collapse to the one-line outcome already being kept for the trace, so the fact
    that they happened survives even when their detail does not."""
    blocks, used, shortened = [], 0, 0
    for entry in reversed(entries):          # spend the budget on the newest first
        whole = f"--- {entry['name']} ({entry['argument']}) RESULT ---\n{entry['result']}"
        if not blocks or used + len(whole) <= budget:
            blocks.append(whole)
            used += len(whole)
        else:
            blocks.append(f"--- {entry['name']} ({entry['argument']}) ---\n"
                          f"[Ran earlier this turn, outcome: {entry['summary']}. The full "
                          "output is no longer shown; call it again if you need it.]")
            shortened += 1
    blocks.reverse()
    if shortened:
        blocks.insert(0, f"[{shortened} earlier tool result(s) shortened to fit the "
                         "context window. What they did is still listed below.]")
    return "\n\n".join(blocks)


# Model output uses typographic punctuation - "can\u2019t", "I\u2019ve" - while every
# pattern below is written with ASCII quotes. Without normalising first, "can't" matches
# and "can\u2019t" does not, which silently disabled three of these detectors.
SMART_PUNCTUATION = str.maketrans({
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...",
})


def plain_text(text):
    return (text or "").translate(SMART_PUNCTUATION)


def unverified_files(reply, trace):
    """Filenames the reply talks about that no tool this turn actually touched."""
    if not CLAIMED_ACTION_RE.search(plain_text(reply)):
        return []
    touched = {Path(n).name.lower() for n in FILENAME_RE.findall(trace or "")}
    mentioned = {Path(n).name for n in FILENAME_RE.findall(reply)}
    return sorted(n for n in mentioned if n.lower() not in touched)


def denoise(text):
    return NOISE_RE.sub("\n", text)


def extract_tool_call(text):
    """Return (TOOL_NAME, argument) or (None, None). Strictest form first."""
    cleaned = denoise(text)

    # WRITE and EDIT are handled first and greedily: their payloads are file content
    # that often contains ']', which would truncate any non-greedy match.
    greedy = TOOL_GREEDY_RE.search(cleaned)
    if greedy:
        action, content = greedy.group(1).upper(), greedy.group(2)
        # Stop at the next tool call if the model emitted several in one reply -
        # otherwise the first one swallows every file after it.
        next_call = NEXT_TOOL_RE.search(content)
        if next_call:
            content = content[:next_call.start()]
        if content.rstrip().endswith("]"):
            content = content.rstrip()[:-1]
        return "FILE_OP", f"{action} | {content.strip()}"

    for pattern in (TOOL_BRACKETED_RE, TOOL_LINE_RE, TOOL_LOOSE_RE):
        match = pattern.search(cleaned)
        if match:
            name = match.group(1).upper()
            argument = match.group(2).strip().rstrip("]").strip()
            # The app logs tool activity as "NAME: arg -> result". Models imitate that
            # shape and send the result back as part of the argument, so cut it off.
            # (WRITE is handled by the greedy branch above, where '->' is legitimate
            # content in code.)
            argument = re.split(r"\s+->\s+", argument, maxsplit=1)[0].strip()
            if name in FILE_OP_ACTIONS.split("|"):
                # [TOOL: MKDIR | path] -> FILE_OP with the action folded back in.
                separator = "|" if argument.startswith("|") else " | "
                return "FILE_OP", f"{name}{separator}{argument.lstrip('|').strip()}"
            return name, argument

    # JSON-ish argument form, e.g. READ_FILE{path: "/x"}.
    brace = BRACE_TOOL_RE.search(cleaned)
    if brace:
        name = brace.group(1).upper()
        argument = _args_from_braces(brace.group(2))
        if name in FILE_OP_ACTIONS.split("|"):
            return "FILE_OP", f"{name} | {argument}"
        return name, argument

    # Nothing matched a real tool. Skills and tools both appear in the prompt, so a
    # skill name used as a tool (e.g. [TOOL: TO_SPEC: ...]) is a common slip - route
    # it rather than letting the raw tag leak to the user.
    unknown = UNKNOWN_TOOL_RE.search(cleaned)
    if unknown:
        raw_name = unknown.group(1)
        looked_up = raw_name.lower().replace("_", "-")
        if looked_up in all_skills():
            return "USE_SKILL", looked_up
        return "__UNKNOWN__", raw_name
    return None, None


def collapse_repetition(text, limit=6):
    """Models sometimes degenerate into repeating one line hundreds of times
    (gemma4 does this in llama.cpp at long context). Collapse the run rather than
    pasting a thousand identical lines into the transcript."""
    lines = text.splitlines()
    output, run_value, run_length, collapsed = [], None, 0, 0
    any_collapsed = False
    for line in lines:
        stripped = line.strip()
        if stripped and stripped == run_value:
            run_length += 1
            if run_length > limit:
                collapsed += 1
                any_collapsed = True
                continue
        else:
            if collapsed:
                output.append(f"    ...[{collapsed} more identical lines removed]")
                collapsed = 0
            run_value, run_length = stripped, 1
        output.append(line)
    if collapsed:
        output.append(f"    ...[{collapsed} more identical lines removed]")
    return "\n".join(output), any_collapsed


def strip_tool_calls(text):
    for pattern in (TOOL_BRACKETED_RE, TOOL_LINE_RE, TOOL_LOOSE_RE,
                    BRACE_TOOL_RE, UNKNOWN_TOOL_RE):
        text = pattern.sub("", text)
    return text.strip()


def store_memories(text):
    """Save [REMEMBER: fact] lines into core memory, then drop them from the reply."""
    text = denoise(text)
    memory = load_memory()
    known = set(memory["core"]) | set(memory["archival"])
    changed = False
    for fact in REMEMBER_RE.findall(text):
        fact = fact.strip()
        if fact and fact not in known:
            memory["core"].append(fact)
            known.add(fact)
            changed = True
    if changed:
        save_json(MEMORY_FILE, memory)
    return REMEMBER_RE.sub("", text).strip()


def store_growth(text):
    """Fold [EVOLVE: TRAIT|OPINION|JOKE: detail] lines into the character file."""
    character = load_character()
    cap = settings().get("max_learned_entries", 30)
    changed = False
    for category, detail in EVOLVE_RE.findall(text):
        key = EVOLVE_KEYS.get(category.upper())
        detail = detail.strip()
        if key and detail and detail not in character.get(key, []):
            character.setdefault(key, []).append(detail)
            character[key] = character[key][-cap:]
            changed = True
    if changed:
        save_json(CHARACTER_FILE, character)
    return EVOLVE_RE.sub("", text).strip()


def store_project_notes(text):
    """Save [PROJECT: fact] lines against the current project, then drop them."""
    for note in PROJECT_NOTE_RE.findall(denoise(text)):
        record_project_note(note)
    return PROJECT_NOTE_RE.sub("", text).strip()


def split_file_op(raw):
    """Split a FILE_OP payload into (action, first argument, second argument).

    EDIT is the one action whose second argument is whitespace-significant: .strip()
    would eat the indentation of the very lines being matched, so only the formatting
    newlines and the single space introduced by the ' | ' delimiter come off."""
    # Accept "MKDIR: /path" as well as "MKDIR | /path" - models mix the two.
    raw = re.sub(rf"^\s*({FILE_OP_ACTIONS})\s*:\s*", r"\1 | ", raw, count=1, flags=re.I)
    parts = raw.split("|", 2)
    action = parts[0].strip().upper()
    first = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""
    if action == "EDIT":
        second = re.sub(r"^[ \t]?\n?", "", rest.rstrip("\n"))
    else:
        second = rest.strip()
    return action, first, second
