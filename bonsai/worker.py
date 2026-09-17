"""The turn loop and the background workers."""

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
import requests
from PyQt6.QtCore import (
    QThread, pyqtSignal,
)
from .config import (
    DEBUG_LOG_FILE, DEFAULTS, MEMORY_FILE,
)
from .store import (
    all_skills, fetch_briefing, load_character, load_memory, load_trusted, record_project_file, record_screen, save_json, save_vault_note, search_memory, search_vault, unreachable_server,
)
from .text import (
    ACTIONS_ECHO_RE, DENIES_TOOL_RE, MAX_CONSECUTIVE_REFUSALS, MAX_IDENTICAL_CALLS, MUTATING_TOOLS, RECORD_ECHO_RE, awaits_an_answer, collapse_repetition, denoise, extract_tool_call, invented_recall, mutation_target, plain_text, render_results, split_file_op, store_growth, store_memories, store_project_notes, strip_result_echoes, strip_tool_calls, unsearched_memory,
)
from .files import (
    _dest_path, fetch_url, find_files, list_directory, path_is_trusted, read_file, resolve_guarded, search_images, search_web,
)
from .codeintel import (
    FILE_OPS, describe_references,
)
from .shell import (
    classify_command, classify_launch, download_file, execute_command, make_pdf, start_program,
)
from .media import (
    LOOK_ALIASES, capture_screen, look_at, make_chart,
)
from .turns import (
    CONTEXT_SAFETY, LEARN_SKILL_PROMPT, accept_learned_skill, handle_task, handoff_from_trace, parse_handoff, save_skill, use_skill,
)
from .chats import (
    search_chats,
)
from .prompt import (
    build_system_prompt, build_tool_schemas, native_call_to_tool, relevant_tools,
)


class Worker(QThread):
    """Runs one turn: query the model, run any tools it asks for, repeat until it
    answers in plain text or the step budget runs out."""

    # reply, tool trace, hit step limit, attempted changes that never landed
    finished = pyqtSignal(str, str, bool, str)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)
    tool_ran = pyqtSignal(str)
    permission_needed = pyqtSignal(str)
    tokens_used = pyqtSignal(int, int)   # prompt tokens, completion tokens
    chunk = pyqtSignal(str)              # a piece of the reply, as it is written
    thinking = pyqtSignal(int)           # reasoning tokens seen so far, before any reply
    preview = pyqtSignal(str)            # a path to put in the preview panel
    handoff = pyqtSignal(str, str)       # why the turn ended early, and where it got to

    def __init__(self, prompt, attach_vision, history, config):
        super().__init__()
        self.prompt = prompt
        self.attach_vision = attach_vision
        self.history = history or []
        self.config = config
        # A tool result can only carry text, so LOOK leaves its picture here for the
        # loop to hand to the next step in the vision slot.
        self.fresh_frame = None
        self.prompt_tokens = 0
        # Grows during the turn: a tool called in text form is loaded natively for
        # every step after it, so needing something absent costs one step, not the turn.
        self.active_tools = relevant_tools(prompt)
        self._gate = None
        self._granted = False
        self._cancelled = False

    def context_is_tight(self):
        """Whether another step would risk the server refusing the request outright.

        Checked against what the server reported loading, not against the -c value in
        a compose file, which may not be what is actually running."""
        limit = self.config.get("context_size", 0)
        if not limit or not self.prompt_tokens:
            return False
        return (self.prompt_tokens + self.config.get("max_tokens", 2048)
                > limit * CONTEXT_SAFETY)

    def cancel(self):
        """Asked to stop. The in-flight HTTP request finishes, but no further model
        calls or tool calls are made."""
        self._cancelled = True
        if self._gate:  # unblock a pending permission prompt
            self._granted = False
            self._gate.set()

    # -- permission bridge (worker thread blocks; GUI thread answers) --

    def ask_permission(self, description):
        self._gate = threading.Event()
        self._granted = False
        self.permission_needed.emit(description)
        answered = self._gate.wait(timeout=120)  # fail closed on timeout
        return answered and self._granted

    def grant_permission(self, granted):
        self._granted = granted
        if self._gate:
            self._gate.set()

    # -- model --

    def model_name(self):
        url = self.config.get("server_url", DEFAULTS["server_url"])
        try:
            response = requests.get(url.replace("/v1/chat/completions", "/v1/models"), timeout=3)
            if response.status_code == 200:
                models = response.json().get("data", [])
                if models and models[0].get("id"):
                    return Path(models[0]["id"]).name
        except Exception:
            pass
        return "the model"

    def stream_reply(self, payload, url, timeout):
        """Read a server-sent reply, emitting the visible text as it arrives.

        The model streams `reasoning_content` first and `content` after it - 59 chunks
        of the former before 10 of the latter, on a short answer - so reasoning is
        counted for the status line and never shown as the reply."""
        payload = {**payload, "stream": True}
        text, reasoned = [], 0
        with requests.post(url, json=payload, timeout=timeout, stream=True) as response:
            if response.status_code != 200:
                raise RuntimeError(f"API error {response.status_code}: {response.text[:300]}")
            for raw in response.iter_lines():
                if self._cancelled:
                    break
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace")
                if not line.startswith("data: "):
                    continue
                body = line[6:]
                if body == "[DONE]":
                    break
                try:
                    delta = json.loads(body)["choices"][0].get("delta") or {}
                except (ValueError, KeyError, IndexError):
                    continue
                if delta.get("reasoning_content"):
                    reasoned += 1
                    if reasoned % 8 == 0:
                        self.thinking.emit(reasoned)
                piece = delta.get("content")
                if piece:
                    text.append(piece)
                    self.chunk.emit(piece)
        return "".join(text)

    def ask_model(self, system_prompt, user_prompt, image=None,
                  temperature=None, with_tools=True, stream=False):
        """`image` is a base64 frame captured once for the whole turn, not per step:
        the answer should describe the screen as it was when the question was asked."""
        history = [{"role": h["role"], "content": h["content"]} for h in self.history]
        # Ordered so the invariant part of a turn comes first: system prompt, then
        # history, then the screenshot - all identical across the steps of one turn -
        # and only the accumulated tool results change at the tail. That is what lets
        # the server reuse the prefix instead of re-reading thousands of image tokens
        # on every step.
        messages = [{"role": "system", "content": system_prompt}] + history
        if image:
            messages.append({"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
                {"type": "text", "text": user_prompt},
            ]})
        else:
            messages.append({"role": "user", "content": user_prompt})

        payload = {
            "messages": messages,
            "max_tokens": self.config.get("max_tokens", 2048),
            "temperature": (self.config.get("temperature", 1.0)
                            if temperature is None else temperature),
            "top_p": self.config.get("top_p", 0.8),
            "top_k": self.config.get("top_k", 20),
            "repeat_penalty": self.config.get("repeat_penalty", 1.0),
            "presence_penalty": self.config.get("presence_penalty", 0.0),
            # Gemma 4 reasons by default under --jinja. Those tokens are spent from
            # max_tokens but never appear in the reply, which produces empty responses
            # and slow turns. Tool selection doesn't need a reasoning trace.
            "chat_template_kwargs": {
                "enable_thinking": self.config.get("enable_thinking", False)},
            # The prefix is stable within a turn now, so reuse it rather than
            # re-prefilling the system prompt and the screenshot on every step.
            "cache_prompt": True,
        }
        chosen = (self.config.get("model") or "").strip()
        if chosen:
            # Only sent when set. llama.cpp ignores it, LM Studio and Ollama use it to
            # decide what to load - and sending an empty string makes Ollama 404.
            payload["model"] = chosen
        if with_tools and self.config.get("native_tools", True):
            payload["tools"] = build_tool_schemas(self.active_tools)
            payload["tool_choice"] = "auto"
        url = self.config.get("server_url", DEFAULTS["server_url"])
        timeout = self.config.get("request_timeout", 300)
        if stream and self.config.get("stream_replies", True):
            return self.stream_reply(payload, url, timeout), None
        response = requests.post(url, json=payload, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text}")
        data = response.json()
        message = data["choices"][0]["message"]
        raw = message.get("content") or ""
        usage = data.get("usage") or {}
        if usage:
            # llama.cpp reports exact counts; no need to estimate.
            self.prompt_tokens = int(usage.get("prompt_tokens", 0))
            self.tokens_used.emit(self.prompt_tokens,
                                  int(usage.get("completion_tokens", 0)))

        # Structured tool call, if the server produced one. This is the whole point of
        # native tools: the call arrives already parsed and schema-validated.
        native = None
        for call in (message.get("tool_calls") or []):
            function = call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {}
            native = native_call_to_tool(function.get("name"), arguments or {})
            break
        try:
            DEBUG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DEBUG_LOG_FILE, "a") as f:
                tag = "NATIVE" if native else "RAW"
                f.write(f"\n=== {datetime.now().isoformat()} {tag} ===\n"
                        f"{native if native else raw!r}\n")
        except Exception:
            pass  # logging must never break a request
        return raw, native

    # -- tools --

    def run_file_op(self, raw):
        action, first, second = split_file_op(raw)

        if not action and not first and not second:
            # Almost always a reply that hit the token cap mid-call, so the tag arrived
            # without its payload. Saying "unknown action" sends it looking for a typo.
            return ("[That FILE_OP arrived empty, which usually means the reply was cut "
                    "off at the token limit before the content was written. NOTHING was "
                    "written. Write a shorter file, or split it across several calls.]")
        if action not in FILE_OPS:
            return f"[Unknown FILE_OP action: {action}]"
        function, two_args, template = FILE_OPS[action]

        preview = second[:200] + ("..." if len(second) > 200 else "")
        description = template.format(a=first, b=preview)

        # Trust is checked on effective destinations. A bare rename target resolves into
        # the source folder, so a rename inside a trusted folder must not prompt.
        if action in ("WRITE", "EDIT"):
            involved = [first]
        elif two_args:
            try:
                destination = str(_dest_path(Path(first).expanduser().resolve(), second))
            except Exception:
                destination = second
            involved = [first, destination]
        else:
            involved = [first]

        trusted = load_trusted()
        auto = bool(trusted) and all(path_is_trusted(p, trusted) for p in involved if p)
        if auto:
            self.status.emit(f"{action.title()} (trusted folder)...")
        else:
            self.status.emit("Waiting for permission...")
            if not self.ask_permission(description):
                return "[The user did not grant permission for this file operation.]"
            self.status.emit(f"{action.title()}...")

        return function(first, second) if two_args else function(first)

    def run_command(self, raw):
        argv, workdir, verdict = classify_command(raw)
        if argv is None:
            return verdict  # refusal message
        if verdict == "ask":
            description = (f"Run a command:\n  {' '.join(argv)}\n\nIn folder:\n  {workdir}\n\n"
                           "This is outside your trusted folders or not on the safe-command "
                           "list.")
            self.status.emit("Waiting for permission...")
            if not self.ask_permission(description):
                return "[The user did not grant permission to run this command.]"
        self.status.emit(f"Running: {' '.join(argv)[:50]}")
        return execute_command(argv, workdir)

    def show_preview(self, raw):
        """Put a file in the side panel for the user to see. Deliberately not the same
        as LOOK: this shows THEM something, LOOK is how the model sees it itself."""
        target = (raw or "").split("|")[0].strip()
        if not target:
            return "[PREVIEW needs a file path.]"
        path, err = resolve_guarded(target)
        if err:
            return err
        if not path.exists():
            return f"[Nothing to preview: {path} does not exist.]"
        if path.is_dir():
            return f"[{path} is a folder. PREVIEW takes one file.]"
        self.preview.emit(str(path))
        return (f"'{path.name}' is now open in the user's Preview panel. They can see "
                "it. This does not show it to YOU - call LOOK if you need to check it "
                "yourself.")

    def launch(self, raw):
        """Opening something on the desktop always asks - there is no trusted-folder
        shortcut, because this is the one tool that reaches outside the sandbox and
        leaves something running once the turn is over."""
        argv, refusal = classify_launch(raw)
        if refusal:
            return refusal
        description = (f"Open on your desktop:\n  {' '.join(argv)}\n\n"
                       "This runs outside the sandbox with access to your session, and "
                       "keeps running after this turn finishes.")
        self.status.emit("Waiting for permission...")
        if not self.ask_permission(description):
            return "[The user did not grant permission to open this.]"
        self.status.emit(f"Opening: {' '.join(argv)[:50]}")
        return start_program(argv)

    def run_tool(self, name, argument):
        if name == "__UNKNOWN__":
            skills = ", ".join(sorted(all_skills())) or "none"
            return (f"[There is no tool called '{argument}'. Skills are not tools - to use a "
                    f"skill, call USE_SKILL with its name. Available skills: {skills}. "
                    "Available tools: SEARCH, FETCH, READ_FILE, LIST_DIR, FILE_OP, DOWNLOAD, "
                    "RUN, LAUNCH, LOOK, PREVIEW, USAGES, TASK, RECALL, HISTORY, SEARCH_VAULT, "
                    "SAVE_NOTE, SEARCH_IMAGES, MAKE_PDF, MAKE_CHART, SAVE_SKILL, "
                    "USE_SKILL.]")
        if name == "SEARCH":
            self.status.emit(f"Searching: {argument}")
            return search_web(argument)
        if name == "FETCH":
            self.status.emit(f"Fetching: {argument}")
            return fetch_url(argument)
        if name == "READ_FILE":
            self.status.emit(f"Reading: {argument}")
            return read_file(argument)
        if name == "LIST_DIR":
            self.status.emit(f"Listing: {argument}")
            return list_directory(argument)
        if name == "FIND":
            self.status.emit(f"Searching for: {argument}")
            return find_files(argument)
        if name == "RECALL":
            self.status.emit(f"Recalling: {argument}")
            return search_memory(argument)
        if name == "HISTORY":
            self.status.emit(f"Searching earlier chats: {argument}")
            return search_chats(argument, self.config.get("chat_id"))
        if name == "SAVE_NOTE":
            self.status.emit("Saving a note to the vault...")
            return save_vault_note(argument)
        if name == "SEARCH_VAULT":
            self.status.emit(f"Searching the vault: {argument}")
            return search_vault(argument)
        if name == "SEARCH_IMAGES":
            self.status.emit(f"Looking for images: {argument}")
            return search_images(argument)
        if name == "MAKE_PDF":
            self.status.emit("Typesetting a PDF...")
            return make_pdf(argument)
        if name == "MAKE_CHART":
            self.status.emit("Drawing a chart...")
            return make_chart(argument)
        if name == "USAGES":
            self.status.emit(f"Finding uses of {argument[:40]}...")
            return describe_references(argument)
        if name == "LOOK":
            self.status.emit(f"Looking at {argument[:40]}...")
            note, picture = look_at(argument)
            if picture:
                self.fresh_frame = picture
                target = argument.split("|")[0].strip()
                if target.lower() not in LOOK_ALIASES:
                    self.preview.emit(target)
            return note
        if name == "PREVIEW":
            return self.show_preview(argument)
        if name == "LAUNCH":
            return self.launch(argument)
        if name == "DOWNLOAD":
            self.status.emit("Downloading...")
            return download_file(argument)
        if name == "RUN":
            return self.run_command(argument)
        if name == "TASK":
            return handle_task(argument)
        if name == "FILE_OP":
            return self.run_file_op(argument)
        if name == "SAVE_SKILL":
            self.status.emit("Saving skill...")
            return save_skill(argument)
        if name == "USE_SKILL":
            self.status.emit(f"Loading skill: {argument}")
            return use_skill(argument)
        return f"[Unknown tool: {name}]"

    # -- main loop --

    def run(self):
        try:
            system_prompt = build_system_prompt(self.prompt)
            max_steps = self.config.get("max_tool_steps", 12)
            label = self.model_name()

            prompt = self.prompt
            # Captured once, up front. Re-capturing per step would answer about a
            # screen that has moved on since the question.
            # The loop runs steady; the closing reply runs in the user's own voice.
            tool_temp = self.config.get("tool_temperature", 0.5)
            voice_temp = self.config.get("temperature", 1.0)
            frame, blind = None, ""
            if self.attach_vision:
                try:
                    frame = capture_screen()
                except Exception as exc:
                    # Losing the screenshot should cost the screenshot, not the turn.
                    blind = (f"\n\n[The screen capture failed: {exc} - you have no image "
                             "this turn. Say so rather than describing a screen you "
                             "cannot see, and use your file tools instead.]")
                    self.tool_ran.emit(f"screen capture failed: {exc}")
            prompt += blind
            keep_frame = self.config.get("vision_all_steps", True)
            reply = ""
            trace = []
            results = []
            call_counts = {}    # (tool, arg) -> how many times it was asked for
            refusals = 0        # consecutive results that were refusals or errors
            looping = False
            unfinished = {}     # target -> why its last mutation attempt failed
            changed = False     # did any mutation actually land this turn
            closed_task = False # did it mark anything DONE
            # Each nudge fires at most once per turn, so none of them can loop.
            corrected = pressed = reminded = searched = recalled = False
            prodded = False
            unattended = self.config.get("unattended", False)
            exhausted = True
            ran_out = ""        # "steps" or "context" - why the turn ended early
            handed_off = False

            for step in range(max_steps):
                if self._cancelled:
                    break
                if step and self.context_is_tight():
                    # Stop while there is still room to write a handoff. Pushing on
                    # until the server refuses the request loses the turn entirely,
                    # and with it everything learned in it.
                    ran_out = "context"
                    break
                self.status.emit(f"Querying {label}..." if step == 0
                                 else f"Step {step + 1}/{max_steps}...")
                reply, native = self.ask_model(system_prompt, prompt, frame,
                                               temperature=tool_temp)
                if not keep_frame:
                    frame = None        # first step only, the old behaviour

                if self._cancelled:
                    break

                # A schema-validated call from the server beats anything regex can
                # recover from free text; fall back to parsing only when absent.
                name, argument = native if native else extract_tool_call(reply)
                if name is None:
                    if unattended and changed and not closed_task and not reminded:
                        # It finishes the work and forgets the bookkeeping, so auto mode
                        # re-runs the same task until it hits the round limit.
                        reminded = True
                        prompt = (
                            f"{prompt}\n\n[You changed files this turn but marked no task "
                            "DONE. If one of your open tasks is now genuinely finished, "
                            "close it with TASK: DONE <id> | <the file you wrote>. Only do "
                            "that for a task whose file you actually wrote - if none is "
                            "finished, say which one is still in progress and keep going.]")
                        self.status.emit("Updating the task list...")
                        continue
                    if unattended and not changed and not pressed:
                        pressed = True
                        opening = ("Nobody is available to answer that."
                                   if awaits_an_answer(reply)
                                   else "Nothing was created or changed this turn.")
                        prompt = (
                            f"{prompt}\n\n[{opening} Listing or closing tasks is not "
                            "progress - writing and changing files is, and that is what "
                            "this turn is missing. You are running unattended, so no one "
                            "will answer a question or pick an option for you: choose the "
                            "most reasonable one yourself, say which in a single line, and "
                            "write the actual code with your tools in this same turn. If "
                            "you need to know what already exists, LIST_DIR or READ_FILE "
                            "it rather than asking. Missing folders are not a blocker - "
                            "MKDIR them. Stop only if you are truly blocked, and then name "
                            "the one thing blocking you.]")
                        self.status.emit("Pushing past a stalled turn...")
                        continue
                    if not prodded and not trace and DENIES_TOOL_RE.search(plain_text(reply)):
                        # It said it could not, without trying. The tools were listed in
                        # the same request it answered.
                        prodded = True
                        prompt = (
                            f"{prompt}\n\n[You said you cannot do that, but you did not "
                            "try. You can run commands on this machine, read and search "
                            "files, and search the web - the tools were available on the "
                            "request you just answered. Use them and answer from what "
                            "they return. Note the shell is not involved, so issue one "
                            "plain command with its own arguments rather than chaining "
                            "with && or piping.]")
                        self.status.emit("It said it couldn't - prompting it to try...")
                        continue
                    if not recalled and unsearched_memory(reply, "\n".join(trace)):
                        # It said it doesn't know, without opening the drawer it was
                        # told about. Make it look before it disappoints the user.
                        recalled = True
                        stored = len(load_memory()["archival"])
                        prompt = (
                            f"{prompt}\n\n[You said you do not know that, but you have "
                            f"{stored} fact(s) in archival memory that you have not "
                            "searched. Core memory is only the small slice kept in your "
                            "prompt - the rest is reachable with the recall tool. Search "
                            "it now with the relevant terms, then answer. If it genuinely "
                            "holds nothing, say so.]")
                        self.status.emit("Checking archival memory...")
                        continue
                    if not searched and invented_recall(reply, "\n".join(trace)):
                        # It answered about another conversation from nothing. It has no
                        # memory of past chats, so this is invention - make it look.
                        searched = True
                        prompt = (
                            f"{prompt}\n\n[You were asked about an earlier conversation "
                            "and answered without looking it up. You have no memory of "
                            "past chats - they exist only in stored transcripts. Call "
                            "the history tool with the relevant terms now, and answer "
                            "from what it returns. If it finds nothing, say plainly that "
                            "you have no record of it rather than describing what it "
                            "might have been.]")
                        self.status.emit("Checking earlier conversations...")
                        continue
                    if unfinished and not corrected:
                        # It stopped talking to tools while a change it attempted never
                        # landed. Left alone it reports that work as done - which is
                        # exactly how a turn ends with a confident summary of files that
                        # are not on disk. It gets one correction, then the truth goes
                        # to the user either way.
                        corrected = True
                        blocked = "\n".join(f"- {why}" for why in unfinished.values())
                        prompt = (
                            f"{prompt}\n\n--- THESE CHANGES DID NOT HAPPEN ---\n{blocked}\n"
                            "Each one failed and you have not retried it since, so it is "
                            "not on disk. Either issue the tool calls that carry it out "
                            "now, or tell the user plainly which ones you could not do "
                            "and why. Do not describe any of them as done.")
                        self.status.emit("Finishing an incomplete change...")
                        continue
                    exhausted = False
                    break

                signature = (name, argument.strip())
                repeats = call_counts.get(signature, 0)
                call_counts[signature] = repeats + 1

                if repeats >= MAX_IDENTICAL_CALLS:
                    # Warning about the repeat twice changed nothing, so this one simply
                    # is not run. A single refused TASK ADD otherwise looped 180 times.
                    result = ("[Not run: you have already made this exact call with these "
                              "exact arguments this turn, and the answer will not change. "
                              "Do something different, or stop and tell the user what is "
                              "blocking you.]")
                else:
                    result = self.run_tool(name, argument)
                    if repeats:
                        result += ("\n\n[You already made this exact call this turn and got "
                                   "the same result. Repeating it will not change anything. "
                                   "Either do something genuinely different, or stop and "
                                   "tell the user what is blocking you.]")

                # Refusals are bracketed by convention; a run of them means the turn is
                # going nowhere, whether or not the calls are identical.
                refusals = refusals + 1 if result.startswith("[") else 0

                if name in MUTATING_TOOLS:
                    target = mutation_target(name, argument)
                    if result.startswith("["):
                        unfinished[target] = f"{name} on {target} - {result.splitlines()[0]}"
                    else:
                        unfinished.pop(target, None)   # a retry landed it after all
                        changed = True
                        record_project_file(target)
                if name == "TASK" and argument.strip().upper().startswith("DONE"):
                    closed_task = True

                self.active_tools.add(name.lower())
                summary = result.splitlines()[0] if result.splitlines() else ""
                line = f"{name}: {argument[:70]} -> {summary[:60]}"
                trace.append(line)
                self.tool_ran.emit(line)

                if self.fresh_frame:
                    # What LOOK rendered becomes what the next step sees. Keeping it
                    # for the rest of the turn is deliberate: the point is to write a
                    # fix while still looking at the thing that is wrong.
                    frame = self.fresh_frame
                    self.fresh_frame = None

                if refusals >= MAX_CONSECUTIVE_REFUSALS:
                    looping = True
                    break

                results.append({"name": name, "argument": argument,
                                "result": result, "summary": summary[:120]})
                prompt = (
                    f"{self.prompt}\n\n"
                    + render_results(results, self.config.get("max_result_chars", 40000))
                    + "\n---\nAbove are all tool results so far this turn. Each tool call "
                      "handles ONE file or action - if the user asked for several, you have "
                      "only done the ones listed above. Call another tool for each remaining "
                      "one. Only answer in plain text once every requested item appears in "
                      "the results above."
                )

            if self._cancelled:
                done = "\n".join(f"- {t}" for t in trace) or "- nothing"
                self.finished.emit(f"(Cancelled.)\n\nWhat ran before stopping:\n{done}",
                                   "\n".join(trace), False,
                                   "\n".join(unfinished.values()))
                return

            if looping:
                # Deliberately no further model call: it is looping, and asking it to
                # summarise would just be one more chance to loop.
                recent = "\n".join(f"- {line}" for line in trace[-6:]) or "- nothing"
                self.finished.emit(
                    "(Stopped: the same calls kept being refused and nothing was getting "
                    "done, so the turn was ended rather than left to repeat.)\n\n"
                    f"The last few calls were:\n{recent}",
                    "\n".join(trace), False, "\n".join(unfinished.values()))
                return

            if exhausted and not ran_out:
                ran_out = "steps"
            if ran_out:
                self.status.emit("Writing a handoff...")
                limit = ("all available steps" if ran_out == "steps"
                         else "nearly all of the context window")
                prompt += (
                    f"\n\nYou have used {limit} for this turn, so it is ending now. "
                    "You have no tools on this reply.\n"
                    "That means you CANNOT read, run or check anything further here. Do "
                    "not write out what a call would have returned, and never write a "
                    "line like '--- READ_FILE (path) RESULT ---': inventing a result is "
                    "worse than stopping, it is detected and removed, and it wastes the "
                    "turn. Anything still to do goes in NEXT below and you will have "
                    "your tools back.\n"
                    "First, tell the user in plain text what you got done and what is "
                    "left. Claim only what the results above actually show.\n"
                    "Then write this block, which is for you, not for them - it is the "
                    "only thing that survives into your next turn:\n"
                    "[HANDOFF]\n"
                    "DONE: what is genuinely finished, with the file paths\n"
                    "LEFT: what still has to happen\n"
                    "NEXT: the single next action, concretely - which tool, which file\n"
                    "[/HANDOFF]")
                reply = strip_tool_calls(self.ask_model(
                    system_prompt, prompt, frame,
                    temperature=voice_temp, with_tools=False, stream=True)[0])
                reply, carried = parse_handoff(reply)
                if not carried:
                    carried = handoff_from_trace("\n".join(trace), ran_out)
                if carried:
                    self.handoff.emit(ran_out, carried)
                    handed_off = True
            elif abs(voice_temp - tool_temp) > 0.01:
                # Everything above ran at the steady temperature so tool choice would
                # not wander; say the answer again in the user's own voice. This is a
                # whole extra generation - prompt caching speeds up prefill, not decode,
                # so it roughly doubles time-to-answer. Setting tool_temperature equal
                # to temperature turns it off.
                self.status.emit("Putting that in your own words...")
                spoken = strip_tool_calls(self.ask_model(
                    system_prompt,
                    prompt + "\n\nNow answer the user in plain text. You have no tools "
                             "on this reply, so do not claim anything the results above "
                             "do not show, and do not write out what a call would have "
                             "returned - a made-up result is detected and removed.",
                    frame, temperature=voice_temp, with_tools=False, stream=True)[0])
                if spoken.strip():
                    reply = spoken       # keep the steady reply if this one comes back empty

            reply = ACTIONS_ECHO_RE.sub("", reply).strip()
            reply = RECORD_ECHO_RE.sub("", reply).strip()
            reply, invented, echoed = strip_result_echoes(reply, "\n".join(trace))
            if invented:
                reply += ("\n\n\u26a0 Removed " + str(len(invented)) + " block(s) written "
                          "to look like tool output - " + ", ".join(invented[:4])
                          + ". Those calls did NOT run this turn, so everything in them "
                          "was made up. Nothing was read from disk.")
                # Inventing a result is what it does when it still has work to do and
                # the closing reply has taken its tools away. Those fabricated calls are
                # therefore an accurate statement of what it wanted to do next, so hand
                # them to a turn that can actually run them rather than losing the work.
                if not handed_off:
                    self.handoff.emit("invented results", (
                        "DONE: only what the tool list above actually shows.\n"
                        "LEFT: " + ", ".join(invented[:6]) + " were claimed but never "
                        "run, so nothing is known about them.\n"
                        "NEXT: actually call " + invented[0] + " and read the real "
                        "result before saying anything about it."))
                    handed_off = True
                    reply += (" Picking that up now with the tools available.")
                else:
                    reply += (" Ask again and watch the tool list for the real calls.")
            elif echoed:
                reply += ("\n\n(Removed " + str(len(echoed)) + " copy of a tool result "
                          "the model pasted back into its reply.)")
            reply, was_repetitive = collapse_repetition(reply)
            if was_repetitive:
                reply += ("\n\n(The model degenerated into repeating itself. Check the "
                          "context indicator - if it is low, this is a model/server issue "
                          "rather than a context one, and restarting the llama.cpp container "
                          "usually clears it.)")
            reply = store_memories(reply)
            reply = store_growth(reply)
            reply = store_project_notes(reply)
            if not reply.strip():
                reply = ("(The model returned an empty reply. This usually means the token "
                         "budget was consumed before it produced any visible text - try "
                         "raising Max response length in Settings, or shortening the request.)")
            self.finished.emit(reply, "\n".join(trace), exhausted,
                               "\n".join(unfinished.values()))
        except requests.exceptions.ReadTimeout:
            self.failed.emit(
                f"Model timed out after {self.config.get('request_timeout', 300)}s. "
                "Large attachments and long replies take a while on a local model - raise "
                "Model request timeout in Settings, lower Max response length, or attach "
                "fewer files.")
        except requests.exceptions.ConnectionError:
            # Almost always a server that is not running, which is ordinary once you
            # have several to switch between. The raw urllib3 text says none of that.
            self.failed.emit(unreachable_server(
                self.config.get("server_url", DEFAULTS["server_url"])))
        except Exception as exc:
            self.failed.emit(f"Error: {exc}")


CONSOLIDATE_PROMPT = (
    "You are tidying an assistant's memory about its user. You will be given a numbered "
    "list of remembered facts. Some are duplicates, near-duplicates, outdated, or trivial.\n"
    "Return ONLY two sections, exactly in this format:\n"
    "KEEP:\n"
    "- <fact, merged and rewritten if several said the same thing>\n"
    "ARCHIVE:\n"
    "- <fact that is still true but too specific or stale to keep in working memory>\n"
    "Rules: keep at most {cap} facts under KEEP. Never invent facts that weren't in the "
    "list. Drop exact duplicates entirely rather than listing them twice. Prefer general, "
    "durable facts in KEEP and one-off details in ARCHIVE."
)


KEEP_RE = re.compile(r"KEEP:\s*(.*?)(?:ARCHIVE:|$)", re.S | re.I)


ARCHIVE_RE = re.compile(r"ARCHIVE:\s*(.*)", re.S | re.I)


def _bullet_list(block):
    return [re.sub(r"^[-*\d.\s]+", "", l).strip()
            for l in block.splitlines() if l.strip() and not l.strip().startswith("#")]


class SkillLearner(QThread):
    """Drafts a skill for a procedure the app has already decided is worth keeping.

    Runs after the reply is delivered, like the memory pass, so nothing waits on it."""

    learned = pyqtSignal(str, str)    # name, description
    rejected = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, config, entry):
        super().__init__()
        self.config = config
        self.entry = entry

    def run(self):
        try:
            asked = "\n".join(f"- {r}" for r in self.entry.get("requests", [])[-4:])
            body = (f"Requests that led to this job:\n{asked}\n\n"
                    f"Tools used, in order: {self.entry['signature'].replace('>', ' then ')}")
            payload = {
                "messages": [
                    {"role": "system",
                     "content": LEARN_SKILL_PROMPT.format(count=self.entry["count"])},
                    {"role": "user", "content": body},
                ],
                "max_tokens": 700,
                "temperature": 0.4,   # writing down what happened, not inventing
                "chat_template_kwargs": {
                    "enable_thinking": self.config.get("enable_thinking", False)},
            }
            url = self.config.get("server_url", DEFAULTS["server_url"])
            response = requests.post(url, json=payload, timeout=120)
            if response.status_code != 200:
                self.failed.emit(f"Skill drafting failed: API {response.status_code}")
                return
            draft = denoise(response.json()["choices"][0]["message"]["content"] or "")
            draft = strip_tool_calls(draft).strip().splitlines()
            draft = next((line for line in draft if line.count("|") >= 2), "")
            name, why = accept_learned_skill(draft, self.entry["signature"])
            if name:
                self.learned.emit(name, draft.split("|")[1].strip())
            else:
                self.rejected.emit(why)
        except Exception as exc:
            self.failed.emit(f"Skill drafting failed: {exc}")


class ConsolidationWorker(QThread):
    """Sleep-time memory pass: merges duplicate core facts and demotes stale ones to
    archival, so core memory stays small enough to live in the system prompt."""

    done = pyqtSignal(int, int)   # kept, archived
    failed = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            memory = load_memory()
            cap = self.config.get("core_memory_cap", 12)
            if len(memory["core"]) <= cap:
                return  # nothing to do

            listing = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(memory["core"]))
            payload = {
                "messages": [
                    {"role": "system", "content": CONSOLIDATE_PROMPT.format(cap=cap)},
                    {"role": "user", "content": listing},
                ],
                "max_tokens": 1500,
                "temperature": 0.3,  # tidying, not creating
                "cache_prompt": False,
            }
            response = requests.post(self.config.get("server_url", DEFAULTS["server_url"]),
                                     json=payload, timeout=120)
            if response.status_code != 200:
                self.failed.emit(f"Consolidation API error {response.status_code}")
                return
            raw = denoise(response.json()["choices"][0]["message"]["content"])

            keep_match, archive_match = KEEP_RE.search(raw), ARCHIVE_RE.search(raw)
            if not keep_match:
                self.failed.emit("Consolidation output wasn't in the expected format.")
                return
            keep = _bullet_list(keep_match.group(1))
            archive = _bullet_list(archive_match.group(1)) if archive_match else []

            # Safety: never let a bad model response delete facts outright. Anything that
            # doesn't survive into KEEP or ARCHIVE is pushed to archival, not dropped.
            original = set(memory["core"])
            accounted = set(keep) | set(archive)
            orphaned = [f for f in memory["core"] if f not in accounted]

            if not keep:
                self.failed.emit("Consolidation returned nothing to keep - skipped.")
                return

            memory["core"] = keep[:cap]
            memory["archival"] = memory["archival"] + archive + orphaned
            # de-duplicate archival while preserving order
            seen, deduped = set(), []
            for fact in memory["archival"]:
                if fact not in seen:
                    seen.add(fact)
                    deduped.append(fact)
            memory["archival"] = deduped
            save_json(MEMORY_FILE, memory)
            self.done.emit(len(memory["core"]), len(original) - len(memory["core"]))
        except Exception as exc:
            self.failed.emit(f"Consolidation failed: {exc}")


OBSERVER_PROMPT = (
    "You are {name}, quietly watching over the user's shoulder while they work. You are "
    "shown a screenshot of their screen.\n\n"
    "Reply in exactly this format, two lines:\n"
    "SEEN: <one short factual line describing what is on screen - the app, the task, "
    "anything notable. This is recorded for your own memory, not shown to the user.>\n"
    "SAY: <either the single word SILENT, or one or two short sentences to say out loud>\n\n"
    "Almost always SAY should be SILENT. Only speak if ALL of these hold:\n"
    "1. Something is clearly wrong, stuck, or about to cause a problem.\n"
    "2. You can say something specific and immediately useful about it.\n"
    "3. You have not already said it recently (see below).\n"
    "Do not speak to be friendly, to praise, or to ask how it's going. Interrupting "
    "someone who is concentrating is costly. When in doubt, SILENT.\n\n"
    "--- THINGS YOU ALREADY SAID RECENTLY (do not repeat these) ---\n{recent}\n"
)


SEEN_RE = re.compile(r"SEEN:\s*(.+?)(?:\n|SAY:|$)", re.I | re.S)


SAY_RE = re.compile(r"SAY:\s*(.+)", re.I | re.S)


class ObserverWorker(QThread):
    """One proactive look at the screen. Emits a comment only if the model decides
    something is genuinely worth interrupting for; otherwise emits nothing."""

    comment = pyqtSignal(str)
    quiet = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, config, recent, record_only=False):
        super().__init__()
        self.config = config
        self.recent = recent
        self.record_only = record_only

    def run(self):
        try:
            name = load_character().get("name", "Bonsai")
            recent = "\n".join(f"- {r}" for r in self.recent) or "- nothing yet"
            image = capture_screen()
            payload = {
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text",
                         "text": OBSERVER_PROMPT.format(name=name, recent=recent)},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
                    ],
                }],
                "max_tokens": 512,
                "temperature": 0.4,  # low: this is a judgement call, not creative writing
                # Gemma 4 thinks by default under --jinja. Reasoning tokens eat the budget
                # without appearing in the reply, which made every check return empty.
                # This is a snap yes/no judgement, so thinking is turned off.
                "chat_template_kwargs": {"enable_thinking": False},
                "cache_prompt": False,
            }
            # Falls back to the main server when no separate observer is configured -
            # and also when the observer service is switched off, since a stale URL
            # pointing at a stopped container just produces connection refused.
            url = (self.config.get("observer_url") or "").strip()
            if not (self.config.get("services") or {}).get("bonsai-observer", False):
                url = ""
            url = url or self.config.get("server_url", DEFAULTS["server_url"])
            response = requests.post(url, json=payload, timeout=90)
            if response.status_code != 200:
                self.failed.emit(f"Observer API error {response.status_code}")
                return

            text = denoise(response.json()["choices"][0]["message"]["content"]).strip()
            try:
                DEBUG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(DEBUG_LOG_FILE, "a") as f:
                    f.write(f"\n=== {datetime.now().isoformat()} OBSERVER ===\n{text!r}\n")
            except Exception:
                pass
            text = strip_tool_calls(text)
            if not text:
                self.failed.emit("Observer returned an empty reply (token budget likely "
                                 "consumed by reasoning - raise max tokens or disable thinking).")
                return

            seen_match, say_match = SEEN_RE.search(text), SAY_RE.search(text)
            # Record what was on screen regardless of whether it decides to speak.
            if seen_match:
                record_screen(seen_match.group(1).strip())
            elif not say_match:
                record_screen(text.splitlines()[0][:200])  # unformatted reply, salvage it

            spoken = say_match.group(1).strip() if say_match else ""
            if self.record_only:
                self.quiet.emit()  # only here to update the screen log
                return
            if not spoken or re.fullmatch(r"[^A-Za-z]*SILENT[^A-Za-z]*", spoken, re.I):
                self.quiet.emit()
                return
            text = spoken
            if len(text) > 400:
                text = text[:400].rsplit(" ", 1)[0] + "..."
            self.comment.emit(text)
        except Exception as exc:
            self.failed.emit(f"Observer failed: {exc}")


class BriefingWorker(QThread):
    """Gathers the briefing off the UI thread. Failures are reported, never raised:
    the window must open whether or not the news did."""

    done = pyqtSignal(dict)
    status = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, interests):
        super().__init__()
        self.interests = interests
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        try:
            briefing = fetch_briefing(self.interests,
                                      progress=self.status.emit,
                                      should_stop=lambda: self._stop)
            self.done.emit(briefing)
        except Exception as exc:
            self.failed.emit(f"Briefing failed: {exc}")
