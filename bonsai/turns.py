"""Tasks, evidence, handing off an unfinished turn, and learning procedures."""

import re
from datetime import datetime, timedelta
from .config import (
    PATTERNS_FILE, SKILLS_FILE, TASKS_FILE,
)
from .store import (
    all_skills, load_json, load_skills, save_json, task_words,
)
from .files import (
    resolve_guarded,
)
from .codeintel import (
    check_syntax,
)


HANDOFF_RE = re.compile(r"\[HANDOFF\](.*?)(?:\[/HANDOFF\]|$)", re.S | re.I)


HANDOFF_FIELD_RE = re.compile(r"^\s*(DONE|LEFT|NEXT)\s*:\s*(.+?)\s*$", re.I | re.M)


MIN_HANDOFF_CHARS = 20


# Stop this far into the context window rather than at the wall. The turn still has to
# generate its handoff, and the next tool result is not going to be smaller.
CONTEXT_SAFETY = 0.9


def parse_handoff(reply):
    """Pull the handoff block out of a reply; returns (cleaned reply, handoff or "")."""
    match = HANDOFF_RE.search(reply or "")
    if not match:
        return (reply or "").strip(), ""
    fields = {key.upper(): value.strip()
              for key, value in HANDOFF_FIELD_RE.findall(match.group(1))}
    cleaned = HANDOFF_RE.sub("", reply).strip()
    if not fields.get("NEXT") or len(match.group(1).strip()) < MIN_HANDOFF_CHARS:
        # A block with no next action is not a handoff, it is a sign-off.
        return cleaned, ""
    ordered = [f"{key}: {fields[key]}" for key in ("DONE", "LEFT", "NEXT") if fields.get(key)]
    return cleaned, "\n".join(ordered)


def handoff_next(summary):
    """The NEXT line of a handoff, normalised for comparison.

    Comparing whole handoffs missed the failure that matters: two rounds that both
    ended on "NEXT: FILE_OP WRITE main.py" without writing it, differing only in
    whether the note said "5652 chars" or "5652 bytes". What it intends to do next is
    the part that has to move."""
    for line in (summary or "").splitlines():
        if line.strip().upper().startswith("NEXT:"):
            return " ".join(line.split()[1:]).lower().strip(" .`'\"")
    return ""


def handoff_from_trace(trace, reason):
    """The fallback handoff: what the tools actually did, which is checkable.

    Worth preferring over a prose summary in one respect - it cannot claim anything
    that did not run."""
    lines = [line for line in (trace or "").splitlines() if line.strip()]
    if not lines:
        return ""
    did = "\n".join(f"  - {line}" for line in lines[-12:])
    return (f"DONE: these tool calls ran before the turn ran out of {reason}:\n{did}\n"
            "LEFT: everything the request asks for that is not in that list.\n"
            "NEXT: LIST_DIR the working folder to see what is really there, then make "
            "the first missing piece.")


MIN_EVIDENCE_BYTES = 50


def similar_task(text, tasks):
    """An existing task that means the same thing as `text`, or None.

    Exact-match deduplication was not enough: the model re-adds its own list with
    "Implement " bolted on the front and a description appended, then marks both
    copies done and counts the work twice."""
    incoming = task_words(text)
    if not incoming:
        return None
    for task in tasks:
        existing = task_words(task["text"])
        if not existing:
            continue
        shared = len(incoming & existing) / min(len(incoming), len(existing))
        if shared >= 0.6:
            return task
    return None


def verify_evidence(raw):
    """Check the file a task is claimed to have produced: (path, None) or (None, why not).

    Marking a task DONE used to cost one tool call and assert a fact nothing checked,
    which is the cheapest possible way to look productive. Eleven tasks were closed in
    one run against four files. Completion now has to point at something real."""
    raw = (raw or "").strip().strip("'\"")
    if not raw:
        return None, ("[Not marked done: TASK DONE needs the file that proves it, e.g. "
                      "TASK: DONE 3 | /path/to/item_manager.gd. If no file proves the "
                      "task is finished, then it is not finished - do the work first, or "
                      "tell the user what is stopping you.]")
    path, err = resolve_guarded(raw)
    if err:
        return None, err
    if not path.is_file():
        return None, (f"[Not marked done: {path} does not exist, so nothing shows that "
                      "task was actually carried out. Write the file first.]")
    size = path.stat().st_size
    if size < MIN_EVIDENCE_BYTES:
        return None, (f"[Not marked done: {path} is only {size} bytes, which is a stub "
                      "rather than finished work. Write the real implementation first.]")
    broken = check_syntax(path)
    if broken:
        # The gate already proved the file exists. A file that cannot be parsed is not
        # evidence of a finished task, it is evidence of an unfinished one.
        return None, (f"[Not marked done: {path} does not parse - {broken}. A file that "
                      "will not load is not finished work. READ_FILE it, fix it with "
                      "EDIT, and mark the task done once it parses.]")
    return path, None


def load_tasks():
    """Tasks carry a stable id. Positional numbering is unsafe: removing task 1 and
    then task 2 in separate calls deletes the wrong second task, because the list
    shifted underneath. Ids never move and are never reused."""
    data = load_json(TASKS_FILE, {"tasks": [], "next_id": 1})
    tasks = data["tasks"]
    changed = False
    next_id = data.get("next_id", 1)
    for task in tasks:  # migrate any pre-id entries
        if "id" not in task:
            task["id"] = next_id
            next_id += 1
            changed = True
        if "evidence" not in task:
            task["evidence"] = ""
            changed = True
    if changed:
        save_json(TASKS_FILE, {"tasks": tasks, "next_id": next_id})
    return tasks


def save_tasks(tasks, next_id=None):
    if next_id is None:
        next_id = max((t.get("id", 0) for t in tasks), default=0) + 1
    save_json(TASKS_FILE, {"tasks": tasks, "next_id": next_id})


def format_tasks(tasks):
    if not tasks:
        return "(the list is empty)"
    lines = []
    for task in tasks:
        mark = "[x]" if task["done"] else "[ ]"
        proof = f"  <- {task['evidence']}" if task.get("evidence") else ""
        lines.append(f"[{task['id']}] {mark} {task['text']}{proof}")
    return "\n".join(lines)


def handle_task(raw):
    """TASK: ADD <text> | LIST | DONE <id> | REMOVE <id> | CLEAR - a scratchpad that
    survives across turns. Ids are stable, so an id always refers to the same task
    no matter what else has been removed."""
    raw = re.sub(r"^\s*(ADD|LIST|DONE|CLEAR_DONE|CLEAR|REMOVE)\s*[:|]\s*", r"\1 ",
                 raw.strip(), count=1, flags=re.I)
    parts = raw.strip().split(None, 1)
    action = parts[0].upper().strip(":|") if parts else ""
    argument = parts[1].strip() if len(parts) > 1 else ""
    tasks = load_tasks()
    by_id = {t["id"]: t for t in tasks}

    if action == "ADD":
        if not argument:
            return "[TASK ADD needs text.]"
        twin = similar_task(argument, tasks)
        if twin:
            state = "already done" if twin["done"] else "already open"
            return (f"[Not added: task {twin['id']} is the same work, and is {state}: "
                    f"\"{twin['text']}\". Do not try to add it again - work on task "
                    f"{twin['id']} itself, or pick a different task from the list.]\n"
                    f"{format_tasks(tasks)}")
        new_id = max((t["id"] for t in tasks), default=0) + 1
        tasks.append({"id": new_id, "text": argument, "done": False, "evidence": ""})
        save_tasks(tasks, new_id + 1)
        return f"Added task {new_id}: {argument}\n{format_tasks(tasks)}"

    if action == "DONE":
        id_part, _, evidence_raw = argument.partition("|")
        wanted = [int(n) for n in re.findall(r"\d+", id_part)]
        if not wanted:
            return (f"[TASK DONE needs a task id and the file that proves it, e.g. "
                    f"TASK: DONE 3 | /path/to/item_manager.gd]\n{format_tasks(tasks)}")
        if len(wanted) > 1:
            return ("[Mark one task done at a time: each needs its own file as evidence. "
                    f"TASK: DONE {wanted[0]} | <path>]\n{format_tasks(tasks)}")
        task = by_id.get(wanted[0])
        if not task:
            return f"[No task with id {wanted[0]}.]\n{format_tasks(tasks)}"
        if task["done"]:
            return (f"[Task {task['id']} was already done - nothing to do.]\n"
                    f"{format_tasks(tasks)}")
        proof, problem = verify_evidence(evidence_raw)
        if problem:
            return f"{problem}\n{format_tasks(tasks)}"
        for other in tasks:
            if other["id"] != task["id"] and other.get("evidence") == str(proof):
                # Exact, unlike the fuzzy duplicate check on ADD: one file closing two
                # tasks means the same work was counted twice.
                return (f"[Not marked done: {proof.name} already closed task "
                        f"{other['id']} (\"{other['text']}\"). One file cannot finish two "
                        "tasks - either this task needs its own file, or it is a "
                        f"restatement of {other['id']} and should be removed.]\n"
                        f"{format_tasks(tasks)}")
        task["done"] = True
        task["evidence"] = str(proof)
        save_tasks(tasks)
        return (f"Marked done: {task['text']} (verified {proof}, "
                f"{proof.stat().st_size} bytes)\n{format_tasks(tasks)}")

    if action == "REMOVE":
        wanted = [int(n) for n in re.findall(r"\d+", argument)]
        if not wanted:
            return f"[TASK REMOVE needs a task id, e.g. TASK: REMOVE 3]\n{format_tasks(tasks)}"
        hit, missed = [], []
        for task_id in dict.fromkeys(wanted):
            task = by_id.get(task_id)
            if not task:
                missed.append(str(task_id))
                continue
            hit.append(task["text"])
            tasks.remove(task)
        if not hit:
            return f"[No task with id {', '.join(missed)}.]\n{format_tasks(tasks)}"
        save_tasks(tasks)
        note = f" (no task with id {', '.join(missed)})" if missed else ""
        return "Removed: " + ", ".join(hit) + note + f"\n{format_tasks(tasks)}"

    if action == "CLEAR_DONE":
        done = [t for t in tasks if t["done"]]
        if not done:
            return f"[No completed tasks to remove.]\n{format_tasks(tasks)}"
        remaining = [t for t in tasks if not t["done"]]
        save_tasks(remaining)
        return (f"Removed {len(done)} completed task(s): "
                + ", ".join(t["text"][:40] for t in done)
                + f"\n{format_tasks(remaining)}")

    if action == "CLEAR":
        save_tasks([], 1)
        return "Task list cleared."

    if action and action not in {"ADD", "LIST", "DONE", "CLEAR", "CLEAR_DONE", "REMOVE"}:
        return (f"[Unknown TASK action '{action}'. Use: TASK: ADD <text>, TASK: LIST, "
                "TASK: DONE <id> | <file>, TASK: REMOVE <id>, TASK: CLEAR_DONE, or "
                "TASK: CLEAR.]")

    return f"Tasks:\n{format_tasks(tasks)}"


def tasks_summary():
    tasks = load_tasks()
    open_tasks = [t for t in tasks if not t["done"]]
    if not open_tasks:
        return ""
    lines = "\n".join(f"[{t['id']}] {t['text']}" for t in open_tasks[:10])
    return (f"--- TASKS STILL OPEN (already on your list, use the id to mark them "
            f"DONE) ---\n{lines}\n\n")


def save_skill(raw):
    parts = [p.strip() for p in raw.split("|", 2)]
    if len(parts) < 3 or not parts[0]:
        return "[SAVE_SKILL failed: expected 'name | description | instructions']"
    name, description, instructions = parts
    data = load_skills()
    data["skills"] = [s for s in data["skills"] if s["name"].lower() != name.lower()]
    data["skills"].append({"name": name, "description": description,
                           "instructions": instructions})
    save_json(SKILLS_FILE, data)
    return f"Skill '{name}' saved."


def use_skill(name):
    skill = all_skills().get(name.strip().lower())
    if not skill:
        return f"[No skill named '{name}'.]"
    out = f"--- Skill: {skill['name']} ---\n{skill['instructions']}"
    if skill.get("extras"):
        # Progressive disclosure: reference files stay out of context until needed.
        listing = "\n".join(f"- {skill['folder']}/{e}" for e in skill["extras"])
        out += ("\n\nSupporting files for this skill (READ_FILE them if you need the "
                f"detail):\n{listing}")
    return out


def skills_summary():
    skills = all_skills().values()
    if not skills:
        return "No saved skills yet."
    return "\n".join(f"- {s['name']}: {s['description']}" for s in skills)


PATTERN_MIN_STEPS = 2          # one tool call is an action, not a procedure


PATTERN_REPEATS = 3            # times a shape must recur before it is worth keeping


MAX_PATTERNS = 60


MAX_LEARNED_SKILLS = 25


MIN_SKILL_INSTRUCTIONS = 80


def load_patterns():
    return load_json(PATTERNS_FILE, {"patterns": []})


def turn_signature(trace):
    """The shape of a turn: which tools, in order, with repeats collapsed.

    FILE_OP carries its action because writing a file and editing one are different
    procedures; consecutive repeats collapse because 'wrote three files' and 'wrote
    five' are the same shape."""
    steps = []
    for line in (trace or "").splitlines():
        match = re.match(r"\s*([A-Z_]{3,16}):\s*(.*)", line)
        if not match:
            continue
        name, rest = match.group(1).lower(), match.group(2)
        if name == "file_op":
            action = re.match(r"\s*([A-Za-z]+)", rest)
            if action:
                name = f"file_op:{action.group(1).lower()}"
        outcome = rest.split("->", 1)[1].strip() if "->" in rest else ""
        if outcome.startswith("["):
            # A refusal or an error is not part of the procedure. The marker is on the
            # RESULT side of the arrow, not the argument side.
            continue
        if not steps or steps[-1] != name:
            steps.append(name)
    return ">".join(steps)


def record_pattern(trace, request):
    """Note that a turn of this shape succeeded. Returns the pattern's new count."""
    signature = turn_signature(trace)
    if signature.count(">") + 1 < PATTERN_MIN_STEPS or not signature:
        return signature, 0
    data = load_patterns()
    patterns = data["patterns"]
    for entry in patterns:
        if entry["signature"] == signature:
            entry["count"] = entry.get("count", 1) + 1
            entry["requests"] = (entry.get("requests", []) + [request[:300]])[-4:]
            entry["last"] = datetime.now().isoformat(timespec="seconds")
            break
    else:
        entry = {"signature": signature, "count": 1, "requests": [request[:300]],
                 "last": datetime.now().isoformat(timespec="seconds"), "skill": None}
        patterns.append(entry)
    data["patterns"] = sorted(patterns, key=lambda p: p.get("count", 0),
                              reverse=True)[:MAX_PATTERNS]
    save_json(PATTERNS_FILE, data)
    return signature, entry["count"]


def pattern_ready(signature):
    """The pattern worth turning into a skill right now, or None.

    Fires once: the entry is stamped when the skill is written, so a procedure used
    fifty times is not re-learned forty-seven times."""
    if len(learned_skills()) >= MAX_LEARNED_SKILLS:
        return None
    for entry in load_patterns()["patterns"]:
        if (entry["signature"] == signature and not entry.get("skill")
                and entry.get("count", 0) >= PATTERN_REPEATS
                and len(entry.get("requests", [])) >= 2):
            return entry
    return None


def learned_skills():
    return [s for s in load_skills()["skills"] if s.get("learned")]


def mark_pattern_learned(signature, skill_name):
    data = load_patterns()
    for entry in data["patterns"]:
        if entry["signature"] == signature:
            entry["skill"] = skill_name
    save_json(PATTERNS_FILE, data)


SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,39}$")


def accept_learned_skill(raw, signature):
    """Validate a drafted skill and save it, or say why it was thrown away.

    The model is drafting prose about its own behaviour, which it will happily do
    badly. Nothing is saved that would not be useful to read back."""
    parts = [part.strip() for part in (raw or "").split("|", 2)]
    if len(parts) < 3:
        return None, "the draft was not 'name | description | instructions'"
    name = re.sub(r"[^a-z0-9-]+", "-", parts[0].lower()).strip("-")[:40]
    description, instructions = parts[1], parts[2]
    if not SKILL_NAME_RE.match(name):
        return None, f"'{parts[0]}' is not a usable skill name"
    if name in all_skills():
        return None, f"a skill called '{name}' already exists"
    if len(instructions) < MIN_SKILL_INSTRUCTIONS:
        return None, "the instructions were too thin to be worth keeping"
    if not description:
        return None, "it had no description"
    data = load_skills()
    data["skills"].append({"name": name, "description": description[:300],
                           "instructions": instructions, "learned": True,
                           "signature": signature,
                           "created": datetime.now().isoformat(timespec="seconds")})
    save_json(SKILLS_FILE, data)
    mark_pattern_learned(signature, name)
    return name, ""


SKILL_BLOCK_RE = re.compile(r"^##\s*(.+?)\s*$", re.M)


def skills_as_text(skills):
    """Skills as an editable document. Round-trips through skills_from_text."""
    blocks = []
    for skill in skills:
        tag = " (learned automatically)" if skill.get("learned") else ""
        blocks.append(f"## {skill['name']}{tag}\n{skill.get('description', '')}\n\n"
                      f"{skill.get('instructions', '').rstrip()}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def skills_from_text(text):
    """Parse the editor back into skills, or return (None, why not).

    Refuses rather than saving something lossy: this is the only place a hand-written
    skill can be destroyed, and a silent mangle would be worse than an error."""
    parts = SKILL_BLOCK_RE.split(text or "")
    if parts and parts[0].strip():
        return None, "there is text before the first '## name' heading"
    skills, seen = [], set()
    for heading, body in zip(parts[1::2], parts[2::2]):
        learned = "(learned automatically)" in heading
        name = heading.replace("(learned automatically)", "").strip().lower()
        if not SKILL_NAME_RE.match(name):
            return None, f"'{heading.strip()}' is not a usable skill name"
        if name in seen:
            return None, f"'{name}' appears twice"
        seen.add(name)
        lines = body.strip().split("\n", 1)
        description = lines[0].strip()
        instructions = (lines[1] if len(lines) > 1 else "").strip()
        if not instructions:
            return None, f"'{name}' has a description but no instructions"
        entry = {"name": name, "description": description, "instructions": instructions}
        if learned:
            entry["learned"] = True
        skills.append(entry)
    return skills, ""


LEARN_SKILL_PROMPT = (
    "You write short reusable procedures for an AI assistant, in its own words.\n\n"
    "The assistant has now done the same shape of job {count} times. Below are the "
    "requests that led to it and the exact sequence of tools it used.\n\n"
    "Write ONE skill capturing how to do this kind of job, as a single line:\n"
    "name | description | instructions\n\n"
    "- name: lowercase words joined by hyphens, e.g. draw-comparison-chart\n"
    "- description: one sentence on when to reach for it\n"
    "- instructions: the actual steps, naming the tools, including anything that has "
    "to happen in a particular order and any mistake worth avoiding. Write it for "
    "someone who has never done it.\n\n"
    "Describe the general procedure, not the specific files. Output the single line "
    "and nothing else - no preamble, no markdown."
)
