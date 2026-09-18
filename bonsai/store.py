"""Everything Bonsai remembers: settings, memory, skills, notes, the vault."""

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
import requests
from .config import (
    BRIEFING_FILE, BRIEFING_SEEN_FILE, CHARACTER_FILE, CHARACTER_SEED, DEFAULTS, INTERESTS_FILE, MEMORY_FILE, PROJECTS_FILE, SCREEN_LOG_FILE, SETTINGS_FILE, SKILLS_FILE, TRUSTED_PATHS_FILE, yaml,
)


def load_json(path, default):
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(default, dict) and isinstance(data, dict):
                for key, value in default.items():
                    data.setdefault(key, value)
            return data
        except Exception:
            pass
    return json.loads(json.dumps(default))


def save_json(path, data):
    """Write JSON so that an interrupted write cannot destroy what was already there.

    Opening a file for writing truncates it immediately, so a crash, a kill, a full
    disk or a serialisation error between that moment and the last byte leaves nothing
    behind. Everything the app owns is saved through here - settings, memory, tasks,
    chats, the project ledger - which makes that window matter far more than its width
    suggests. The new bytes go to a temporary file beside the target, are forced to
    disk, and only then replace it, so a reader sees either the old file or the new one.

    The temporary is named so it cannot be mistaken for content: the chat list globs
    for "*.json" and must not find a half-written one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.saving-{os.getpid()}")
    try:
        with open(temporary, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())    # a rename is only atomic if the bytes are on disk
        os.replace(temporary, path)
    except OSError:
        # Never worse than it used to be: if the swap cannot happen (a locked file on
        # Windows, say) fall back to the write this replaced, which is what would have
        # run anyway. Callers do not guard save_json, so raising here would take out
        # whatever was in the middle of saving.
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    finally:
        try:
            temporary.unlink()      # only still there if the replace never happened
        except OSError:
            pass


def settings():
    return load_json(SETTINGS_FILE, DEFAULTS)


def save_settings(data):
    save_json(SETTINGS_FILE, data)


def load_character():
    if not CHARACTER_FILE.exists():
        save_json(CHARACTER_FILE, CHARACTER_SEED)
    return load_json(CHARACTER_FILE, CHARACTER_SEED)


def load_memory():
    """Tiered memory, MemGPT-style: 'core' is small and always in the system prompt,
    'archival' is unbounded and only reached via the RECALL tool. Older flat
    {"user_facts": [...]} files are migrated into core on first load."""
    memory = load_json(MEMORY_FILE, {"core": [], "archival": []})
    if memory.get("user_facts"):  # migrate legacy format
        memory["core"] = memory.get("core", []) + memory.pop("user_facts")
        save_json(MEMORY_FILE, memory)
    memory.setdefault("core", [])
    memory.setdefault("archival", [])
    return memory


def load_screen_log():
    return load_json(SCREEN_LOG_FILE, {"entries": []})["entries"]


def record_screen(note):
    """Append a one-line description of what was on screen. Both the proactive
    observer and normal vision messages write here, so the assistant accumulates
    a picture of what the user has been doing instead of seeing each frame cold."""
    note = note.strip()
    if not note:
        return
    entries = load_screen_log()
    if entries and entries[-1]["note"] == note:
        return  # unchanged screen, don't pad the log
    entries.append({"at": datetime.now().isoformat(timespec="minutes"), "note": note})
    save_json(SCREEN_LOG_FILE, {"entries": entries[-60:]})


MEMORY_CORE_HEADER = "# CORE - always in the system prompt. Keep this short."


MEMORY_ARCHIVAL_HEADER = "# ARCHIVAL - reached only by the RECALL tool."


def memory_to_text(memory):
    """Render memory for the editor: two headed sections, one fact per line."""
    return (f"{MEMORY_CORE_HEADER}\n" + "\n".join(memory.get("core", []))
            + f"\n\n{MEMORY_ARCHIVAL_HEADER}\n"
            + "\n".join(memory.get("archival", [])) + "\n")


def text_to_memory(text):
    """Parse the editor back. Any '#' line is a header; one naming ARCHIVAL switches
    section, so facts move between the two lists by moving lines between them."""
    core, archival = [], []
    target = core
    for line in (text or "").splitlines():
        fact = line.strip()
        if not fact:
            continue
        if fact.startswith("#"):
            target = archival if "ARCHIVAL" in fact.upper() else core
            continue
        target.append(fact)
    # De-duplicate while preserving order, and never let a fact sit in both lists.
    seen, clean_core, clean_archival = set(), [], []
    for fact in core:
        if fact not in seen:
            seen.add(fact)
            clean_core.append(fact)
    for fact in archival:
        if fact not in seen:
            seen.add(fact)
            clean_archival.append(fact)
    return {"core": clean_core, "archival": clean_archival}


def search_terms(query):
    """Words worth searching for, each paired with its singular/plural variants.

    Plain substring matching missed "gpus" against a fact reading "GPU 0", which looks
    to the user exactly like the memory not being there at all."""
    terms = []
    for word in re.split(r"\W+", (query or "").lower()):
        if len(word) <= 2:
            continue
        variants = {word}
        if word.endswith("es") and len(word) > 4:
            variants.add(word[:-2])
        if word.endswith("s") and len(word) > 3:
            variants.add(word[:-1])
        else:
            variants.add(word + "s")
        terms.append(variants)
    return terms


def matches_terms(text, terms):
    lowered = text.lower()
    return sum(1 for variants in terms if any(v in lowered for v in variants))


# Archival memory small enough to hand over whole rather than search.
RECALL_SHOW_ALL_CHARS = 3000


# ...and small enough that it may as well simply be in the prompt. The core/archival
# split exists to keep an unbounded store out of the context window; below this size
# the split only creates a way for the model to not look, which it takes - asked what
# graphics card the user has, it invented one rather than calling recall.
ARCHIVAL_INLINE_CHARS = 4000


# When it does not fit, this much of it is retrieved for the current question instead.
ARCHIVAL_RETRIEVED_CHARS = 2000


def retrieve_archival(user_prompt, archival, budget=None):
    """Facts worth putting in front of the model for this particular question.

    Scored against the user's own words, then topped up with the most recent, so the
    block is never empty - a question whose wording happens to match nothing should
    still see something rather than nothing."""
    budget = ARCHIVAL_RETRIEVED_CHARS if budget is None else budget
    terms = search_terms(user_prompt or "")
    # Sorted descending, so a higher index must mean newer: -index put the OLDEST
    # facts first, which is exactly backwards when ties are broken by recency.
    scored = sorted(((matches_terms(fact, terms), index, fact)
                     for index, fact in enumerate(archival)), reverse=True)
    chosen, used = [], 0
    for _score, _order, fact in scored:
        if used + len(fact) + 3 > budget:
            break
        chosen.append(fact)
        used += len(fact) + 3
    return chosen


def search_memory(query):
    """RECALL tool: find older facts about the user.

    Matching here is lexical, and lexical matching cannot do synonyms - "graphics card"
    will never find "GPU 0", and a confident "nothing matches" is worse than no search
    at all, because it reads as proof the fact was never stored. The model is the better
    semantic matcher, so whenever archival memory is small enough to fit, it is handed
    over whole and the matching is left to the reader."""
    archival = load_memory()["archival"]
    if not archival:
        return ("[Archival memory is empty - nothing has been moved out of core memory "
                "yet, so everything you have been told is already in your prompt.]")

    listing = "\n".join(f"- {fact}" for fact in archival)
    if len(listing) <= RECALL_SHOW_ALL_CHARS:
        return ("All of archival memory (it is small, so here is the whole of it - judge "
                f"for yourself what is relevant):\n{listing}")

    terms = search_terms(query)
    if not terms:
        return "[Give RECALL a search term.]"
    scored = [(matches_terms(f, terms), f) for f in archival]
    hits = [f for score, f in sorted(scored, key=lambda s: s[0], reverse=True) if score]
    if hits:
        return "Archival memory matches:\n" + "\n".join(f"- {h}" for h in hits[:15])

    # No literal match is not the same as nothing being there, so show the most recent
    # rather than reporting an absence the wording of the query may have invented.
    recent, used = [], 0
    for fact in reversed(archival):
        if used + len(fact) > RECALL_SHOW_ALL_CHARS:
            break
        recent.append(fact)
        used += len(fact)
    recent.reverse()
    return (f"[No fact literally contains '{query}', but the wording may simply differ. "
            f"The {len(recent)} most recent of {len(archival)} stored facts:]\n"
            + "\n".join(f"- {fact}" for fact in recent))


def endpoints():
    """The servers Bonsai can be pointed at, always at least one.

    A single server_url is treated as one unnamed endpoint, so everything downstream
    can assume a list and nobody has to special-case the common setup."""
    config = settings()
    found = []
    for entry in config.get("endpoints") or []:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url.startswith("http"):
            continue            # a line that is not a server is not an endpoint
        found.append({"name": str(entry.get("name") or url).strip(),
                      "url": url,
                      "model": str(entry.get("model") or "").strip(),
                      "compose": str(entry.get("compose") or "").strip()})
    if not found:
        found = [{"name": "Server",
                  "url": config.get("server_url", DEFAULTS["server_url"]),
                  "model": str(config.get("model") or "").strip(),
                  "compose": str(config.get("compose_path") or "").strip()}]
    return found


def search_backend_up(timeout=3):
    """Whether the SEARCH tool has anything to talk to.

    Asked of the server rather than inferred from the services setting: search does not
    have to be in a compose file at all - it is better off in its own, since it has
    nothing to do with which model is loaded - and a checkbox says nothing about
    whether the thing is actually running."""
    url = settings().get("searxng_url", DEFAULTS["searxng_url"])
    try:
        response = requests.get(url, params={"q": "ping", "format": "json"},
                                timeout=timeout)
        return response.status_code < 500
    except Exception:
        return False


def compose_services(path):
    """Service names a compose file actually defines, or [] if it cannot be read.

    Asking docker for a service the file does not have fails the whole command with
    "no such service", so a compose file that only brings up a model server must not
    be asked for a search backend as well."""
    if not path:
        return []               # an endpoint with no compose file defines no services
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    if yaml is not None:
        try:
            loaded = yaml.safe_load(text) or {}
            if isinstance(loaded.get("services"), dict):
                return [str(name) for name in loaded["services"]]
        except Exception:
            pass                # fall through to the text scan rather than give up
    # No YAML parser, or the file did not parse: read the one block we care about.
    # The indent of the first service is what a service looks like; anything deeper is
    # one of its keys, and "environment" is not a service.
    names, inside, indent = [], False, None
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if not inside or not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            break               # a new top-level key ends the services block
        found = re.match(r"^([ \t]+)([A-Za-z0-9._-]+):\s*$", line)
        if not found:
            continue
        depth, name = found.group(1), found.group(2)
        if indent is None:
            indent = depth
        if depth == indent:
            names.append(name)
    return names


def compose_for(url=None):
    """The compose file that starts the server at `url`.

    Each server can name its own, because one compose file stopped being the answer
    the moment there was more than one server to switch between. Falls back to the
    single compose_path setting, which is what a one-server setup has."""
    config = settings()
    here = url or config.get("server_url", DEFAULTS["server_url"])
    listed = endpoints()
    match = next((e for e in listed if e["url"] == here), None)
    if match and match["compose"]:
        return match["compose"]
    if match and any(e["compose"] for e in listed):
        # Once servers name their own files, a blank column means this server has none:
        # a remote API, or one started by hand. Handing it the shared setting instead
        # would let switching away from it stop a container belonging to someone else,
        # or start a server nobody asked for. Where no server names a file, the single
        # setting is still what everything uses.
        return ""
    return config.get("compose_path", "")


def endpoints_as_text(items):
    """One endpoint per line, as 'Name | url | model'. The model is optional."""
    lines = []
    for entry in items:
        row = f"{entry['name']} | {entry['url']}"
        if entry.get("compose"):
            row += f" | {entry.get('model', '')} | {entry['compose']}"
        elif entry.get("model"):
            row += f" | {entry['model']}"
        lines.append(row)
    return "\n".join(lines) + ("\n" if lines else "")


def endpoints_from_text(text):
    """Parse the editor back, or return (None, why not).

    Refuses rather than dropping a malformed line: silently discarding a server
    someone typed is worse than telling them the line is wrong."""
    found, seen = [], set()
    for number, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            return None, f"line {number} needs at least 'Name | url'"
        name, url = parts[0], parts[1]
        model = parts[2] if len(parts) > 2 else ""
        compose = parts[3] if len(parts) > 3 else ""
        if not name:
            return None, f"line {number} has no name"
        if not url.startswith("http"):
            return None, f"line {number}: {url!r} is not a URL"
        if name.lower() in seen:
            return None, f"'{name}' appears twice"
        seen.add(name.lower())
        found.append({"name": name, "url": url, "model": model, "compose": compose})
    return found, ""


def unreachable_server(url):
    """What to say when nothing answers at `url`.

    Names the server as it appears in the picker, says which other ones are configured,
    and - when there is exactly one alternative - which one to pick. A stack trace
    about urllib3 tells you none of that."""
    here = next((e for e in endpoints() if e["url"] == url), None)
    name = f"'{here['name']}'" if here else url
    others = [e["name"] for e in endpoints() if e["url"] != url]
    message = (f"Nothing is answering at {name} ({url}). The server is probably not "
               "running.")
    if len(others) == 1:
        message += f" You can switch to '{others[0]}' in the header."
    elif others:
        message += (" You can switch in the header to: " + ", ".join(others) + ".")
    else:
        message += " Check Server URL in \u2699 Settings \u2192 Model."
    return message


def available_models(server_url, timeout=4):
    """Model ids an OpenAI-compatible server will accept, newest API shape first.

    Every server answers /v1/models: llama.cpp lists the single model it loaded,
    LM Studio and Ollama list everything they could load on demand."""
    url = re.sub(r"/v1/chat/completions/?$", "/v1/models", server_url or "")
    if not url.startswith("http"):
        return []
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception:
        return []
    found = []
    for entry in (data.get("data") or data.get("models") or []):
        name = entry.get("id") or entry.get("name") if isinstance(entry, dict) else entry
        if name and name not in found:
            found.append(str(name))
    return found


def load_skills():
    return load_json(SKILLS_FILE, {"skills": []})


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


MAX_SKILL_CHARS = 12000


def _parse_skill_md(path):
    """Parse an Agent Skills SKILL.md: YAML frontmatter with name/description,
    markdown body as the instructions. Only the two required keys are read, so no
    YAML dependency is needed."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    meta, body = match.groups()

    fields = {}
    key = None
    for line in meta.splitlines():
        if re.match(r"^\s+", line) and key:      # continuation of a folded value
            fields[key] += " " + line.strip()
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            # ">-", ">", "|-", "|" introduce a folded/literal block; the text is on
            # the following indented lines, so the marker itself isn't content.
            if value in (">", ">-", ">+", "|", "|-", "|+"):
                value = ""
            fields[key] = value.strip("'\"")

    name = fields.get("name") or path.parent.name
    description = fields.get("description", "")
    body = body.strip()
    if len(body) > MAX_SKILL_CHARS:
        body = body[:MAX_SKILL_CHARS] + "\n...[skill truncated]"

    extras = sorted(
        str(p.relative_to(path.parent))
        for p in path.parent.rglob("*")
        if p.is_file() and p.name != "SKILL.md"
    )[:20]
    return {"name": name, "description": description, "instructions": body,
            "folder": str(path.parent), "extras": extras}


def discover_skill_folders():
    """Find skills installed by the `npx skills` CLI and friends."""
    found = {}
    for root in settings().get("skill_dirs", DEFAULTS["skill_dirs"]):
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for skill_md in sorted(base.rglob("SKILL.md"))[:200]:
            parsed = _parse_skill_md(skill_md)
            if parsed:
                found[parsed["name"].lower()] = parsed
    return found


def all_skills():
    """JSON skills plus discovered SKILL.md folders, folder skills taking priority."""
    merged = {}
    for skill in load_skills()["skills"]:
        merged[skill["name"].lower()] = skill
    merged.update(discover_skill_folders())
    return merged


# A slash only opens the drawer at the start of the text or after a space, so a path
# like /home/you never looks like the beginning of a skill name.
SKILL_TOKEN_RE = re.compile(r"(?:^|(?<=\s))/([A-Za-z0-9._-]*)$")


def skill_query(text, cursor):
    """The (start, query) of the /token the cursor sits in, or None.

    Only what is behind the cursor matters: editing the middle of an already typed
    word should still offer completions for that word."""
    before = (text or "")[:max(cursor, 0)]
    match = SKILL_TOKEN_RE.search(before)
    if not match:
        return None
    return match.start(), match.group(1)


def match_skills(query, skills):
    """Skills matching `query`, best first.

    Ranked rather than filtered: a name that starts with what was typed is almost
    always the one meant, so it must not sit below a skill that merely mentions the
    word somewhere in its description."""
    wanted = (query or "").strip().lower().replace("_", "-")
    ranked = []
    for name in sorted(skills):
        skill = skills[name] or {}
        description = str(skill.get("description", ""))
        haystack = name.replace("_", "-")
        if not wanted:
            rank = 1
        elif haystack.startswith(wanted):
            rank = 0
        elif wanted in haystack:
            rank = 1
        elif wanted in description.lower():
            rank = 2
        else:
            continue
        ranked.append((rank, name, description))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return [(name, description) for _, name, description in ranked]


def expand_skill_shortcut(prompt, skills=None):
    """Turn a leading '/skill-name' into the instruction it stands for.

    The drawer would otherwise only be autocomplete for a word the model is free to
    ignore. Naming the tool here is the difference between asking and arranging: by
    the time the model sees the turn, using the skill is the request."""
    text = (prompt or "").lstrip()
    match = re.match(r"^/([A-Za-z0-9._-]+)\s*(.*)$", text, re.S)
    if not match:
        return prompt
    known = all_skills() if skills is None else skills
    name = match.group(1).lower().replace("_", "-")
    if name not in known:
        return prompt
    rest = match.group(2).strip()
    instruction = (f'Use your "{name}" skill for this: call USE_SKILL with "{name}" '
                   "before doing anything else, then follow what it says.")
    return f"{instruction}\n\n{rest}" if rest else instruction


BRIEFING_KINDS = ("news", "videos")
# Everything SearXNG will group for us. "general" is ordinary web results - blog posts
# and documentation, which is where most of the useful writing about a niche topic is.
BRIEFING_ALL_KINDS = ("news", "videos", "images", "general")
BRIEFING_RANGES = ("day", "week", "month", "year", "")   # "" means no limit
# What the setting shows against what SearXNG is sent.
BRIEFING_RANGE_LABELS = {"Past day": "day", "Past week": "week", "Past month": "month",
                         "Past year": "year", "Any time": ""}


def briefing_kinds():
    """The kinds to gather, ignoring anything the setting names that does not exist."""
    wanted = settings().get("briefing_kinds") or list(BRIEFING_KINDS)
    kept = [kind for kind in wanted if kind in BRIEFING_ALL_KINDS]
    return kept or list(BRIEFING_KINDS)


# Stories stay "already seen" for this long. Old enough and a piece is worth surfacing
# again - and without an expiry the list would grow forever and slowly starve the
# briefing of anything to show.
BRIEFING_SEEN_DAYS = 45
BRIEFING_SEEN_CAP = 2000


def load_briefing_seen():
    """{url: iso date} of what past briefings have already put in front of you."""
    return load_json(BRIEFING_SEEN_FILE, {"urls": {}}).get("urls", {})


def remember_briefing_urls(urls):
    """Record what was shown, dropping entries old enough to be worth seeing again."""
    seen = load_briefing_seen()
    today = datetime.now()
    cutoff = (today - timedelta(days=BRIEFING_SEEN_DAYS)).date().isoformat()
    fresh = {url: when for url, when in seen.items() if when >= cutoff}
    for url in urls:
        fresh[url] = today.date().isoformat()
    if len(fresh) > BRIEFING_SEEN_CAP:
        keep = sorted(fresh.items(), key=lambda pair: pair[1], reverse=True)
        fresh = dict(keep[:BRIEFING_SEEN_CAP])
    save_json(BRIEFING_SEEN_FILE, {"urls": fresh})
    return fresh


def forget_briefing_seen():
    save_json(BRIEFING_SEEN_FILE, {"urls": {}})


# Words that are ordinary in another Latin-script language and rare in English. Two or
# more of them in one title is a strong signal; one is not, because "de" and "la" turn
# up in English titles often enough to matter.
FOREIGN_MARKERS = {
    # Spanish
    "los", "las", "una", "por", "para", "que", "como", "con", "del", "esto", "muy",
    # German
    "und", "der", "die", "das", "ein", "eine", "mit", "auf", "ist", "nicht", "wie",
    # French
    "les", "une", "des", "dans", "pour", "avec", "est", "sur", "vous", "cette",
    "mais", "peu", "sans", "tout", "aussi", "donc", "alors", "quoi", "faire", "jeu",
    "cette", "leur", "nous", "chez", "toujours",
    # Italian
    "che", "sono", "questo", "della", "gioco", "fare", "delle", "nemmeno", "anche",
    "questa", "degli", "sulla", "nella", "essere",
    # Portuguese
    "voce", "nao", "seu", "uma", "com", "isso", "jogo", "fazer", "muito", "agora",
    "sobre", "pela", "pelo", "seus", "esta",
    # German - the largest gap in practice, since a lot of Linux coverage is German
    "den", "dem", "einen", "einem", "fuer", "von", "auch", "aber", "oder", "noch",
    "schon", "sich", "nach", "beim", "unter", "durch", "wird", "werden", "kann",
    "neue", "neuen", "mehr", "dass", "bringt", "erhaelt", "seine", "ihre", "zum",
    "zur", "bei", "aus", "als",
    # Polish
    "jak", "pod", "nie", "jest", "dla", "teraz", "przez", "oraz", "tego", "juz",
    # unambiguous content words, safe because they are not English at all
    "juego", "hacer", "mucho", "todo", "spiel", "machst", "kannst", "sehr",
    "meine", "forma", "correcta", "instalar",
}


def looks_non_latin(text):
    """True when the writing system itself is not Latin - Cyrillic, Arabic, CJK and
    so on. High confidence, and the case SearXNG's language= fails to filter."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    foreign = sum(1 for ch in letters if not ("a" <= ch.lower() <= "z"
                                              or unicodedata.name(ch, "").startswith("LATIN")))
    return foreign / len(letters) > 0.15


def accent_count(text):
    """Latin letters carrying a diacritic. English essentially never stacks these up;
    Spanish, French and Portuguese do it constantly."""
    return sum(1 for ch in text
               if ch.isalpha() and not ("a" <= ch.lower() <= "z")
               and unicodedata.name(ch, "").startswith("LATIN"))


def looks_english(text, extra=""):
    """Best-effort check that a result is English.

    `extra` is the summary. Judging on the title alone let a lot through: a German
    headline may contain one flaggable word while its summary is three sentences of
    unmistakable German, and "radiolinux 15 agosto" is too short to judge at all until
    you read the Italian paragraph under it.

    Deliberately lenient: titles are short and full of proper nouns, so this rejects
    only on strong evidence - losing an English article is worse than keeping a Spanish
    one. Note what is NOT used here: a dictionary check. pyspellchecker's English word
    list accepts "como", "con", "para", "que" and "una", so a Spanish sentence scores
    0.73 "English" by that measure. Diacritics and function words discriminate; the
    dictionary does not."""
    whole = f"{text} {extra}".strip()
    if looks_non_latin(text) or looks_non_latin(whole):
        return False
    accents = accent_count(whole)
    words = [w.lower() for w in re.findall(r"[A-Za-z']{2,}", whole)]
    markers = sum(1 for w in words if w in FOREIGN_MARKERS)
    if accents >= 3:
        return False                     # café and naïve happen; three do not
    if markers >= 2:
        return False
    if accents >= 2 and markers >= 1:
        return False
    return True


BRIEFING_PAUSE = 1.5        # seconds between upstream queries, to stay welcome


MAX_INTERESTS = 20


def load_interests():
    return load_json(INTERESTS_FILE, {"interests": []})["interests"]


def save_interests(interests):
    cleaned, seen = [], set()
    for topic in interests:
        topic = " ".join(str(topic).split())
        if topic and topic.lower() not in seen:
            seen.add(topic.lower())
            cleaned.append(topic)
    # Cap once, then save and return the same list - returning the uncapped one left
    # the box showing topics that were never written.
    cleaned = cleaned[:MAX_INTERESTS]
    save_json(INTERESTS_FILE, {"interests": cleaned})
    return cleaned


def load_briefing():
    return load_json(BRIEFING_FILE, {"fetched": "", "topics": []})


def briefing_is_stale(max_age_hours=None):
    """True when the cache is old enough to be worth refetching."""
    if max_age_hours is None:
        max_age_hours = settings().get("briefing_max_age_hours", 6)
    fetched = load_briefing().get("fetched")
    if not fetched:
        return True
    try:
        age = datetime.now() - datetime.fromisoformat(fetched)
    except ValueError:
        return True
    return age.total_seconds() >= max_age_hours * 3600


def suggested_interests():
    """A starting point drawn from what is already known, so the first briefing is
    not empty. Only a suggestion - the list is the user's to edit."""
    found = []
    for root, project in load_projects()["projects"].items():
        name = Path(root).name.replace("-", " ").replace("_", " ").strip()
        if name and name.lower() not in {"home", "documents", "downloads"}:
            found.append(name)
    for fact in load_memory()["core"][:6]:
        words = [w for w in re.findall(r"[A-Za-z][\w+#.-]{2,}", fact)
                 if w.lower() not in TASK_NOISE and len(w) > 3]
        if len(words) >= 2:
            found.append(" ".join(words[:3]))
    return found[:6]


def _briefing_query(topic, kind, per_topic, time_range):
    """One SearXNG query. Returns a list of plain dicts, or [] on any failure -
    a briefing is a convenience and must never be able to break startup."""
    config = settings()
    try:
        response = requests.get(
            config.get("searxng_url", DEFAULTS["searxng_url"]),
            params={"q": topic, "format": "json", "categories": kind,
                    "time_range": time_range,
                    # Passed anyway for the engines that honour it; most do not,
                    # which is why the results are filtered again below.
                    "language": "en" if config.get("briefing_english_only", True) else "all"},
            headers={"User-Agent": "Mozilla/5.0 (Bonsai/1.0)"}, timeout=20)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []
    english_only = config.get("briefing_english_only", True)
    items = []
    for result in results[:per_topic * 6]:
        url = (result.get("url") or "").strip()
        title = " ".join((result.get("title") or "").split())
        if not url or not title:
            continue
        summary = " ".join((result.get("content") or "").split())
        if english_only and not looks_english(title, summary):
            continue
        items.append({
            "title": title[:160],
            "url": url,
            "source": (result.get("engine") or "").replace(" news", "").replace(" videos", ""),
            "published": (str(result.get("publishedDate") or "")[:10]),
            "summary": summary[:280],
            "thumbnail": result.get("thumbnail") or "",
            "kind": kind,
        })
        if len(items) >= per_topic:
            break
    return items


def fetch_briefing(interests=None, progress=None, should_stop=None):
    """Gather news and video results for each interest and cache them.

    Everything shown is a real result with a real link - nothing here is written by
    the model, because a fabricated headline would be indistinguishable from a real
    one and there is no tool result to check it against."""
    config = settings()
    interests = interests if interests is not None else load_interests()
    per_topic = config.get("briefing_per_topic", 4)
    time_range = config.get("briefing_time_range", "month")
    kinds = briefing_kinds()
    # What earlier briefings already showed. Held for the whole run so a story cannot
    # slip back in under a second interest.
    already = load_briefing_seen() if config.get("briefing_no_repeats", True) else {}
    seen, topics, repeats = set(), [], 0
    for topic in interests:
        if should_stop and should_stop():
            break
        gathered = []
        for kind in kinds:
            if should_stop and should_stop():
                break
            if progress:
                progress(f"{topic} - {kind}")
            for item in _briefing_query(topic, kind, per_topic, time_range):
                if item["url"] in seen:
                    continue        # the same story surfaces under several interests
                seen.add(item["url"])
                if item["url"] in already:
                    repeats += 1
                    continue
                gathered.append(item)
            time.sleep(BRIEFING_PAUSE)
        topics.append({"topic": topic, "items": gathered})
    shown = [item["url"] for entry in topics for item in entry["items"]]
    if config.get("briefing_no_repeats", True):
        remember_briefing_urls(shown)
    briefing = {"fetched": datetime.now().isoformat(timespec="minutes"),
                "topics": topics, "repeats_skipped": repeats,
                "kinds": kinds, "time_range": time_range}
    save_json(BRIEFING_FILE, briefing)
    return briefing


def load_trusted():
    return load_json(TRUSTED_PATHS_FILE, {"paths": []})["paths"]


PROJECT_FILE_CAP = 60       # files remembered per project


PROJECT_NOTE_CAP = 25       # durable notes remembered per project


PROJECT_FILES_SHOWN = 40    # files listed in the system prompt


def load_projects():
    return load_json(PROJECTS_FILE, {"projects": {}})


def trusted_roots():
    roots = []
    for raw in load_trusted():
        try:
            roots.append(str(Path(raw).expanduser().resolve()))
        except Exception:
            continue
    return roots


def project_root_for(path):
    """The trusted folder a path sits inside, or None. Work outside a trusted folder is
    a one-off, not part of a project, so it isn't recorded."""
    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        return None
    for root in trusted_roots():
        if resolved == Path(root) or Path(root) in resolved.parents:
            return root
    return None


def record_project_file(path):
    """Note that this project contains this file.

    Called automatically whenever a mutation succeeds, so the ledger never depends on
    the model choosing to write it down - which is the part it reliably forgets."""
    root = project_root_for(path)
    if not root:
        return
    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            return                      # folders are structure, not work product
        relative = str(resolved.relative_to(root))
    except Exception:
        return
    data = load_projects()
    project = data["projects"].setdefault(root, {"files": {}, "notes": []})
    project["files"][relative] = datetime.now().isoformat(timespec="minutes")
    if len(project["files"]) > PROJECT_FILE_CAP:
        newest = sorted(project["files"].items(), key=lambda item: item[1])
        project["files"] = dict(newest[-PROJECT_FILE_CAP:])
    project["updated"] = datetime.now().isoformat(timespec="minutes")
    save_json(PROJECTS_FILE, data)


def active_project():
    """The project being worked in: the trusted folder whose ledger was touched most
    recently, falling back to the first trusted folder.

    A folder with nothing recorded in it loses to one that has files or notes, however
    recently it was touched. Only writes add to a ledger, so reading documents in
    another trusted folder stamped its timestamp while recording nothing - and that
    empty folder then outranked the project actually being worked on. The prompt went
    on to describe a folder containing no files, and the real project's ledger, which
    named the exact source file the work was about, was never mentioned at all. The
    model then hunted for that file in the only place the prompt pointed at."""
    roots = trusted_roots()
    if not roots:
        return None
    projects = load_projects()["projects"]
    def rank(root):
        entry = projects.get(root, {})
        return (bool(entry.get("files") or entry.get("notes")),
                entry.get("updated", ""))
    return max(roots, key=rank)


def record_project_note(note):
    note = note.strip()
    root = active_project()
    if not note or not root:
        return False
    path = project_note_path(root)
    if path:
        # With a vault configured the markdown file is the store, so the note is
        # readable, linkable and editable rather than buried in a JSON array.
        if note in read_project_note(root):
            return False
        if not path.is_file():
            refresh_project_note(root)
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"- {note}\n")
            # Register the project even though no file has been written yet: the
            # ledger entry is what tells project_summary this project exists at all.
            data = load_projects()
            entry = data["projects"].setdefault(root, {"files": {}, "notes": []})
            entry["updated"] = datetime.now().isoformat(timespec="minutes")
            save_json(PROJECTS_FILE, data)
            return True
        except Exception:
            pass        # fall through to JSON rather than losing the note
    data = load_projects()
    project = data["projects"].setdefault(root, {"files": {}, "notes": []})
    if note in project["notes"]:
        return False
    project["notes"].append(note)
    project["notes"] = project["notes"][-PROJECT_NOTE_CAP:]
    project["updated"] = datetime.now().isoformat(timespec="minutes")
    save_json(PROJECTS_FILE, data)
    return True


# Everything between these markers is regenerated from the verified ledger; anything
# written outside them - by the model or by you in Obsidian - is left alone.
NOTE_AUTO_START = "<!-- bonsai:files -->"


NOTE_AUTO_END = "<!-- /bonsai:files -->"


MAX_NOTE_CHARS = 4000


def vault_root():
    raw = (settings().get("vault_path") or "").strip()
    return Path(raw).expanduser() if raw else None


def project_note_path(root=None):
    """The markdown file holding a project's notes. Plain markdown in a folder, which
    is all an Obsidian vault is - so Obsidian reads and edits the same file."""
    vault = vault_root()
    root = root or active_project()
    if not vault or not root:
        return None
    return vault / "Bonsai" / f"{Path(root).name or 'Project'}.md"


def _split_note(text):
    """Return (auto block, everything else) from a note's current contents."""
    if NOTE_AUTO_START in text and NOTE_AUTO_END in text:
        head, _, rest = text.partition(NOTE_AUTO_START)
        auto, _, tail = rest.partition(NOTE_AUTO_END)
        return auto.strip("\n"), (head + tail).strip()
    return "", text.strip()


def read_project_note(root=None):
    path = project_note_path(root)
    if not path or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def project_prose(root=None):
    """The part of the note that isn't the generated file list - the actual thinking."""
    prose = _split_note(read_project_note(root))[1]
    for line in ("# " + Path(root or active_project() or "").name, "## Notes"):
        if prose.startswith(line):
            prose = prose[len(line):].strip()
    return prose[:MAX_NOTE_CHARS]


def save_project_note(text, root=None):
    """Write the note back verbatim - used by the notes pane, so hand edits stick."""
    path = project_note_path(root)
    if not path:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except Exception:
        return False


def refresh_project_note(root=None, listing=None):
    """Regenerate only the generated block, preserving everything written around it."""
    path = project_note_path(root)
    root = root or active_project()
    if not path or not root:
        return None
    auto = "\n".join(listing or []) or "_No files recorded here yet._"
    existing = read_project_note(root)
    if existing:
        _old, prose = _split_note(existing)
    else:
        prose = f"# {Path(root).name}\n\n## Notes\n"
        # Carry across anything that was recorded before there was a vault.
        for note in load_projects()["projects"].get(root, {}).get("notes", []):
            prose += f"- {note}\n"
    body = (f"{NOTE_AUTO_START}\n## Files Bonsai has written\n{auto}\n"
            f"_Generated - edits here are overwritten._\n{NOTE_AUTO_END}\n\n{prose.strip()}\n")
    save_project_note(body, root)
    return path


def vault_note_path(title):
    """Where a named note lives. Kept under one subfolder so Bonsai's writes never
    scatter through a vault that also holds your own notes."""
    vault = vault_root()
    if not vault:
        return None
    safe = re.sub(r"[^\w \-.()\[\]]+", "", (title or "").strip()).strip(" .")
    if not safe:
        return None
    folder = settings().get("vault_subfolder", "Bonsai")
    return vault / folder / f"{safe[:80]}.md"


VAULT_INDEX_SHOWN = 40


VAULT_HITS = 6


VAULT_SNIPPET = 400


VAULT_RESULT_CHARS = 3000


def vault_notes():
    """Every note in the vault, newest first. Includes the user's own notes, not only
    the ones Bonsai wrote - the point of pointing it at a vault is that it can read
    what is already there."""
    vault = vault_root()
    if not vault or not vault.is_dir():
        return []
    found = []
    for path in vault.rglob("*.md"):
        if ".obsidian" in path.parts or ".trash" in path.parts:
            continue
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    found.sort(reverse=True)
    return [path for _mtime, path in found]


def vault_index():
    """A list of note titles for the system prompt. Titles only: it is a cheap index
    telling the model what exists, and search_vault fetches the content on demand."""
    notes = vault_notes()
    if not notes:
        return ""
    vault = vault_root()
    shown = notes[:VAULT_INDEX_SHOWN]
    titles = "\n".join(f"- {note.relative_to(vault).with_suffix('')}" for note in shown)
    more = (f"\n(and {len(notes) - len(shown)} more)"
            if len(notes) > len(shown) else "")
    return (f"--- YOUR VAULT: {vault} ({len(notes)} notes, newest first) ---\n"
            f"{titles}{more}\n"
            "This is the user's Obsidian vault and your own long-term notes. Search it "
            "with search_vault before answering anything they may have written down, and "
            "before researching something from scratch. Titles above are relative to the "
            "vault folder - to open one, give READ_FILE the full path starting with the "
            "vault folder above, and add .md.\n\n")


def search_vault(query):
    """SEARCH_VAULT: <terms> - look through every note in the vault."""
    notes = vault_notes()
    if not notes:
        vault = vault_root()
        return ("[No vault configured - set Vault folder in Settings.]" if not vault
                else f"[No notes in {vault} yet.]")
    terms = search_terms(query)
    if not terms:
        return "[Give SEARCH_VAULT something to search for.]"
    vault = vault_root()
    hits = []
    for note in notes:
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        score = matches_terms(f"{note.stem} {text}", terms)
        if score:
            hits.append((score, note, text))
    if not hits:
        return (f"[Nothing in {len(notes)} vault note(s) mentions '{query}'. The wording "
                "may differ - the note titles are listed in your prompt.]")

    hits.sort(key=lambda hit: hit[0], reverse=True)
    blocks, used = [], 0
    for _score, note, text in hits[:VAULT_HITS]:
        lowered = text.lower()
        first = min((lowered.find(v) for group in terms for v in group
                     if lowered.find(v) >= 0), default=0)
        start = max(0, first - 120)
        snippet = " ".join(text[start:start + VAULT_SNIPPET].split())
        block = (f"--- {note} ---\n"
                 f"{'...' if start else ''}{snippet}"
                 f"{'...' if start + VAULT_SNIPPET < len(text) else ''}")
        if used + len(block) > VAULT_RESULT_CHARS and blocks:
            break
        blocks.append(block)
        used += len(block)
    return ("Vault notes matching that:\n\n" + "\n\n".join(blocks)
            + "\n\n[READ_FILE any of these paths for the whole note.]")


def save_vault_note(raw):
    """SAVE_NOTE: <title> | <markdown> - write a note into the Obsidian vault.

    Explicit, because the [PROJECT: ...] tag only fires when the model decides to use
    it. This is what "save that to Obsidian" should reach."""
    parts = [part.strip() for part in (raw or "").split("|", 1)]
    title = parts[0]
    body = parts[1] if len(parts) > 1 else ""
    if not title:
        return "[SAVE_NOTE needs a title: SAVE_NOTE: <title> | <markdown body>]"
    if not body:
        return "[SAVE_NOTE needs something to write after the title.]"
    path = vault_note_path(title)
    if not path:
        return ("[No vault configured - set Vault folder in Settings before saving "
                "notes to Obsidian.]")
    stamp = datetime.now().isoformat(timespec="minutes")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            existing = path.read_text(encoding="utf-8", errors="replace")
            if body.strip() in existing:
                return f"[Already in '{path.name}' - not written twice.]"
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"\n\n---\n*Added {stamp}*\n\n{body.rstrip()}\n")
            return f"Appended to note '{path}' ({len(body)} characters)."
        # The model usually opens with its own H1; two identical headings look broken.
        first = body.lstrip().splitlines()[0].lstrip("# ").strip().lower()
        heading = "" if first == title.strip().lower() else f"# {title}\n"
        path.write_text(f"{heading}*Written by Bonsai, {stamp}*\n\n{body.strip()}\n",
                        encoding="utf-8")
        return f"Created note '{path}' ({len(body)} characters)."
    except Exception as exc:
        return f"[Could not write the note: {exc}]"


def project_summary():
    """The project block for the system prompt: what this project contains and what is
    known about it, carried across chats.

    Every remembered file is checked against disk as it is listed, and ones that have
    gone are dropped from the ledger. A remembered list that has drifted from the
    filesystem would be worse than none - inventing progress is the failure this whole
    app is built to prevent, so the memory of it is not allowed to become a source."""
    root = active_project()
    if not root:
        return ""
    data = load_projects()
    project = data["projects"].get(root)
    prose_now = project_prose(root)
    if not project and not prose_now:
        return ""
    # A vault note can exist before anything has been written here, and it must still
    # reach the prompt - a note the model never sees is not memory.
    project = project or {"files": {}, "notes": []}

    listed, gone = [], []
    newest_first = sorted(project["files"].items(), key=lambda item: item[1], reverse=True)
    for relative, touched in newest_first:
        full = Path(root) / relative
        try:
            if not full.is_file():
                gone.append(relative)
                continue
            listed.append(f"- {relative} ({full.stat().st_size} bytes, last touched {touched})")
        except Exception:
            gone.append(relative)
    if gone:                            # self-healing: renamed, deleted or moved away
        for relative in gone:
            project["files"].pop(relative, None)
        save_json(PROJECTS_FILE, data)
    if not listed and not project["notes"] and not prose_now:
        return ""

    block = f"--- THIS PROJECT: {root} ---\n"
    if listed:
        shown = listed[:PROJECT_FILES_SHOWN]
        block += ("Files you have written here, confirmed on disk just now"
                  f"{f' ({len(listed)} total, newest {len(shown)} shown)' if len(listed) > len(shown) else ''}:\n"
                  + "\n".join(shown) + "\n")
    else:
        block += "You have not written any files here yet.\n"
    prose = prose_now
    if prose:
        block += f"What you know about this project:\n{prose}\n"
    elif project["notes"]:
        block += ("What you know about this project:\n"
                  + "\n".join(f"- {note}" for note in project["notes"]) + "\n")
    if project_note_path(root):
        refresh_project_note(root, listed[:PROJECT_FILES_SHOWN])
    block += ("This survives across chats, so it is what you built before this "
              "conversation started. READ_FILE one before changing it rather than "
              "assuming what is in it, and do not rebuild something already listed.\n\n")
    return block


def save_trusted(paths):
    save_json(TRUSTED_PATHS_FILE, {"paths": paths})


# Words that carry no meaning when comparing two task descriptions.
TASK_NOISE = {
    "implement", "create", "add", "build", "make", "write", "set", "up", "system",
    "layer", "module", "the", "and", "for", "with", "its", "that", "this", "into",
    "using", "via", "from", "new", "core", "basic", "simple", "support", "handling",
}


def task_words(text):
    return {word for word in re.findall(r"[a-z]+", (text or "").lower())
            if len(word) > 2 and word not in TASK_NOISE}
