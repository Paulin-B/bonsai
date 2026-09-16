"""Conversations on disk, including branches."""

import re
from datetime import datetime, timedelta
from .config import (
    CHATS_DIR, CHAT_INDEX_FILE,
)
from .store import (
    load_json, matches_terms, save_json, search_terms, settings,
)
from .text import (
    ACTIONS_ECHO_RE,
)


MAX_HISTORY_HITS = 6


MAX_HISTORY_CHARS = 3000


def search_chats(query, current_id=None):
    """HISTORY: <terms> - look through earlier conversations.

    Chats were being written and never read back, so anything decided in a previous
    conversation was simply gone. This is the only route to it: the transcripts are far
    too large to carry in the system prompt, so they are searched on demand instead."""
    terms = search_terms(query)
    if not terms:
        return "[Give HISTORY something to search for.]"
    titles = {c["id"]: c for c in load_chat_index()["chats"]}
    hits = []
    for path in (sorted(CHATS_DIR.glob("*.json")) if CHATS_DIR.is_dir() else []):
        chat_id = path.stem
        if chat_id == current_id:
            continue                      # the current conversation is already in context
        for message in load_chat(chat_id):
            text = ACTIONS_ECHO_RE.sub("", message.get("content", "")).strip()
            if not text:
                continue
            score = matches_terms(text, terms)
            if score:
                hits.append((score, chat_id, message.get("role", "?"), text))
    if not hits:
        searched = len(titles) - (1 if current_id in titles else 0)
        return (f"[Nothing in {max(searched, 0)} earlier chat(s) mentions '{query}'. It "
                "may never have been discussed, or may be in this conversation already.]")

    hits.sort(key=lambda hit: hit[0], reverse=True)
    lines, used = [], 0
    for _score, chat_id, role, text in hits[:MAX_HISTORY_HITS]:
        chat = titles.get(chat_id, {})
        when = (chat.get("updated") or chat_id)[:16]
        speaker = "The user" if role == "user" else "You"
        snippet = " ".join(text.split())
        if len(snippet) > 600:
            snippet = snippet[:600] + "..."
        block = f'From "{chat.get("title", chat_id)}" ({when}) - {speaker} said:\n{snippet}'
        if used + len(block) > MAX_HISTORY_CHARS and lines:
            break
        lines.append(block)
        used += len(block)
    return ("Earlier conversations mentioning that:\n\n" + "\n\n".join(lines)
            + "\n\n[These are past conversations, not the current one. Treat them as what "
              "was said then, and check anything that may have changed since.]")


def load_chat_index():
    return load_json(CHAT_INDEX_FILE, {"chats": []})


def load_chat(chat_id):
    return load_json(CHATS_DIR / f"{chat_id}.json", {"messages": []})["messages"]


def save_chat(chat_id, messages):
    cap = settings().get("max_history_messages", 20)
    save_json(CHATS_DIR / f"{chat_id}.json", {"messages": messages[-cap:]})


def new_chat():
    chat_id = datetime.now().strftime("chat-%Y%m%d-%H%M%S-%f")
    index = load_chat_index()
    index["chats"].insert(0, {"id": chat_id, "title": "New Chat",
                              "updated": datetime.now().isoformat()})
    save_json(CHAT_INDEX_FILE, index)
    save_chat(chat_id, [])
    return chat_id


def fork_chat(chat_id, upto):
    """A new chat holding the first `upto` messages of this one.

    Branching rather than editing in place: the turn that went wrong is often worth
    keeping to compare against, and a flat history forces a choice between living with
    a bad exchange in context and throwing the whole conversation away."""
    messages = load_chat(chat_id)[:max(upto, 0)]
    index = load_chat_index()
    parent = next((c for c in index["chats"] if c["id"] == chat_id), None)
    stem = re.sub(r" \(branch \d+\)$", "", (parent or {}).get("title", "Chat"))
    siblings = sum(1 for c in index["chats"] if c.get("parent") == chat_id)
    forked = datetime.now().strftime("chat-%Y%m%d-%H%M%S-%f")
    index["chats"].insert(0, {"id": forked,
                              "title": f"{stem} (branch {siblings + 1})"[:60],
                              "updated": datetime.now().isoformat(),
                              "parent": chat_id})
    save_json(CHAT_INDEX_FILE, index)
    save_chat(forked, messages)
    return forked


def title_chat(chat_id, first_message):
    index = load_chat_index()
    for chat in index["chats"]:
        if chat["id"] == chat_id and chat.get("title") == "New Chat":
            title = first_message.strip().replace("\n", " ")
            chat["title"] = (title[:40] + "...") if len(title) > 40 else (title or "New Chat")
    save_json(CHAT_INDEX_FILE, index)


def touch_chat(chat_id):
    index = load_chat_index()
    for chat in index["chats"]:
        if chat["id"] == chat_id:
            chat["updated"] = datetime.now().isoformat()
    index["chats"].sort(key=lambda c: c["updated"], reverse=True)
    save_json(CHAT_INDEX_FILE, index)


def delete_chat(chat_id):
    index = load_chat_index()
    index["chats"] = [c for c in index["chats"] if c["id"] != chat_id]
    save_json(CHAT_INDEX_FILE, index)
    path = CHATS_DIR / f"{chat_id}.json"
    if path.exists():
        path.unlink()
