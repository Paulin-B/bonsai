"""Does it parse, and what else would this change break?"""

from .text import RESULT_ECHO_HEADER_RE

import ast
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from .config import (
    BACKUP_DIR, MAX_READ_BYTES, PROTECTED_PATHS, tomllib, yaml,
)
from .store import (
    load_trusted,
)
from .files import (
    NUMBERED_LINE_RE, _backup, do_copy, do_delete, do_mkdir, do_move, do_rename, is_sensitive, resolve_guarded,
)


SYNTAX_CHECK_MAX_BYTES = 2_000_000


NODE_CHECK_TIMEOUT = 15


# `import`/`export` at the top level is ordinary module code, but node reads a bare
# .js as CommonJS and calls it a syntax error. Check those as modules instead.
ESM_RE = re.compile(r"^\s*(?:import\s|import\{|export\s|export\{)", re.M)


def _check_python(path, text):
    try:
        ast.parse(text)
    except SyntaxError as exc:
        where = f"line {exc.lineno}" if exc.lineno else "unknown line"
        if exc.offset:
            where += f", column {exc.offset}"
        return f"{exc.msg} ({where})"
    except ValueError as exc:
        return str(exc)
    return ""


def _check_json(path, text):
    try:
        json.loads(text)
    except ValueError as exc:
        return str(exc)
    return ""


def _check_toml(path, text):
    if tomllib is None:
        return ""
    try:
        tomllib.loads(text)
    except Exception as exc:
        return " ".join(str(exc).split())
    return ""


def _check_yaml(path, text):
    if yaml is None:
        return ""
    try:
        yaml.safe_load(text)
    except Exception as exc:
        return " ".join(str(exc).split())[:300]
    return ""


def _node_error(stderr):
    """Node prints 'path:line', the offending source, a caret, then the error."""
    lines = [line.rstrip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        return "syntax error"
    message = next((line.strip() for line in lines if "Error: " in line), "syntax error")
    spot = re.search(r":(\d+)$", lines[0])
    return message + (f" (line {spot.group(1)})" if spot else "")


def _check_javascript(path, text):
    node = shutil.which("node")
    if not node:
        return ""
    target, scratch = path, None
    if path.suffix.lower() == ".js" and ESM_RE.search(text):
        scratch = path.with_name(path.name + ".bonsai-check.mjs")
        try:
            scratch.write_text(text, encoding="utf-8")
            target = scratch
        except OSError:
            scratch = None
    try:
        done = subprocess.run([node, "--check", str(target)], capture_output=True,
                              text=True, timeout=NODE_CHECK_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return ""            # a checker that cannot run must not fail the write
    finally:
        if scratch is not None:
            try:
                scratch.unlink()
            except OSError:
                pass
    return "" if done.returncode == 0 else _node_error(done.stderr)


# Only formats with a parser that is actually installed. A suffix that is absent here
# is simply not checked - claiming to verify something and not doing it would be worse
# than saying nothing.
SYNTAX_CHECKERS = {
    ".py": _check_python, ".pyw": _check_python,
    ".json": _check_json,
    ".toml": _check_toml,
    ".yaml": _check_yaml, ".yml": _check_yaml,
    ".js": _check_javascript, ".mjs": _check_javascript, ".cjs": _check_javascript,
}


def check_syntax(path, text=None):
    """The first syntax error in a file, or "" if it parses or has no checker."""
    path = Path(path)
    checker = SYNTAX_CHECKERS.get(path.suffix.lower())
    if checker is None:
        return ""
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    if len(text) > SYNTAX_CHECK_MAX_BYTES or not text.strip():
        return ""
    try:
        return checker(path, text)
    except Exception:
        # A checker that breaks must never turn a good write into a reported failure.
        return ""


def syntax_warning(path, text=None):
    """The note appended to a successful write when what landed does not parse."""
    problem = check_syntax(path, text)
    if not problem:
        return ""
    return (f" WARNING: it does not parse - {problem}. The file IS on disk, but it is "
            "broken, so this is NOT finished work. Read it back, fix it with EDIT now, "
            "and do not mark anything done until it parses.")


try:
    from tree_sitter_language_pack import get_parser as _ts_parser
except ImportError:
    _ts_parser = None


# Deliberately excludes HLSL: its grammar reports an error on an ordinary `cbuffer`
# declaration, so it would be wrong about the first line of most shaders.
TS_LANGUAGES = {
    ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl", ".geom": "glsl",
    ".tesc": "glsl", ".tese": "glsl", ".comp": "glsl", ".vsh": "glsl", ".fsh": "glsl",
    ".wgsl": "wgsl",
    ".gd": "gdscript",
    ".lua": "lua",
    ".css": "css", ".scss": "css",
    ".html": "html", ".htm": "html",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".rs": "rust", ".go": "go", ".java": "java", ".cs": "c_sharp",
    ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "javascript",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".sql": "sql", ".xml": "xml",
    # Present for the reference index, not for syntax: these already have exact
    # checkers above, and introduced_syntax_error skips anything that does.
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
}


# Languages where `x = 1` introduces x. In C, GLSL and friends a name is introduced by
# a declaration and a bare assignment is a USE - counting it as a definition meant
# renaming `varying vec2 texcoord;` looked harmless while `texcoord = ...` remained.
ASSIGNMENT_DEFINES = {"python", "gdscript", "lua", "javascript", "typescript", "tsx",
                      "ruby", "bash"}


TS_MAX_BYTES = 1_000_000


TS_SNIPPET = 60


def ts_parse(path, text):
    """The parsed tree, or None when there is no grammar for this file type."""
    if _ts_parser is None:
        return None
    language = TS_LANGUAGES.get(Path(path).suffix.lower())
    if language is None or len(text) > TS_MAX_BYTES:
        return None
    try:
        return _ts_parser(language).parse(text.encode("utf-8", errors="replace"))
    except Exception:
        return None


def ts_language(path):
    return TS_LANGUAGES.get(Path(path).suffix.lower())


def ts_error_nodes(tree):
    """Every ERROR and MISSING node, outermost first."""
    if tree is None:
        return []
    found, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            found.append(node)
            continue          # its children are noise once the parent is an error
        if node.has_error:    # only descend where something is actually wrong
            stack.extend(reversed(node.children))
    return found


def ts_error_marks(path, text):
    """Position-independent fingerprints of the parse errors in `text`.

    Keyed on the offending source rather than on line numbers, because inserting a
    line above an existing error would otherwise look like a brand new one."""
    marks = []
    for node in ts_error_nodes(ts_parse(path, text)):
        snippet = " ".join(
            text[node.start_byte:node.end_byte].split())[:TS_SNIPPET]
        marks.append((node.type, snippet, node.start_point[0] + 1))
    return marks


def introduced_syntax_error(path, before, after):
    """A parse error present in `after` that was not in `before`, or ""."""
    suffix = Path(path).suffix.lower()
    if before is None or suffix not in TS_LANGUAGES or suffix in SYNTAX_CHECKERS:
        # A suffix with an exact parser has already been judged by it, and judged
        # better - no second, vaguer opinion needed.
        return ""
    old = Counter((kind, snippet) for kind, snippet, _ in ts_error_marks(path, before))
    for kind, snippet, line in ts_error_marks(path, after):
        if old[(kind, snippet)]:
            old[(kind, snippet)] -= 1      # already there before this change
            continue
        where = f"line {line}"
        shown = f": {snippet}" if snippet else ""
        return f"{'missing ' + kind if kind != 'ERROR' else 'parse error'} at {where}{shown}"
    return ""


# Node types that mean "this identifier is being introduced here" rather than used.
# Matched as substrings because grammars name them differently - function_definition,
# function_declaration, func_definition, method_declaration all mean the same thing.
DEFINING_PARENTS = (
    "declaration", "definition", "declarator", "parameter", "field_declaration",
    "function_item", "struct_item", "enum_item", "const_item", "static_item",
    "class_definition", "assignment", "variable", "type_definition", "signature",
)


IDENTIFIER_TYPES = ("identifier", "type_identifier", "field_identifier",
                    "property_identifier", "constant", "name")


# Files worth searching. Anything else is data or output, and matching a symbol there
# says nothing about what would break.
CODE_SUFFIXES = set(TS_LANGUAGES) | set(SYNTAX_CHECKERS) | {
    ".jsx", ".vue", ".svelte", ".glslinc", ".inc", ".shader", ".hlsl", ".fx"}


MAX_INDEXED_FILES = 400


MAX_REFERENCE_HITS = 60


def symbol_occurrences(path, text):
    """Every use of every identifier in one file: {name: [(line, is_definition)]}.

    Falls back to a word-boundary scan when there is no grammar, which still beats
    nothing - it just cannot tell a mention in a comment from a real reference."""
    found = {}
    tree = ts_parse(path, text)
    assigns = ts_language(path) in ASSIGNMENT_DEFINES
    if tree is None:
        for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text):
            line = text.count("\n", 0, match.start()) + 1
            found.setdefault(match.group(0), []).append((line, False))
        return found

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in IDENTIFIER_TYPES and node.child_count == 0:
            name = text[node.start_byte:node.end_byte]
            if name:
                parent = node.parent.type if node.parent else ""
                defines = any(mark in parent for mark in DEFINING_PARENTS
                              if mark != "assignment" or assigns)
                found.setdefault(name, []).append((node.start_point[0] + 1, defines))
        else:
            stack.extend(node.children)
    return found


SKIP_FOLDERS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build",
                "dist", ".mypy_cache", ".pytest_cache", "target", ".bonsai_backups"}


MAX_WALKED_DIRS = 600


PROJECT_MARKERS = {".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
                   "project.godot", "shaders", "Makefile", "CMakeLists.txt"}


def project_root(path, roots):
    """The folder a file's siblings live in - its project, not every trusted folder.

    References to a shader varying are in the shader pack beside it. Searching every
    trusted folder instead walked 10,000 files on another disk to answer a question
    about two files in one directory."""
    start = Path(path).expanduser()
    start = start if start.is_dir() else start.parent
    allowed = [Path(root).expanduser() for root in roots]
    here = start
    for _ in range(8):
        if not here.is_dir() or here.parent == here:
            break
        try:
            if any((here / marker).exists() for marker in PROJECT_MARKERS):
                return here
        except OSError:
            break
        if any(here == root for root in allowed):
            return here
        if not any(root in here.parents for root in allowed):
            break
        here = here.parent
    return start


def code_files(roots):
    """Source files under the given folders, pruning as it walks.

    rglob descends into .git and node_modules whatever the filter says afterwards, so
    the pruning has to happen during the walk, not after it."""
    seen, files, walked = set(), [], 0
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        # Resolved live rather than matched by name: BACKUP_DIR is configurable, and
        # every write puts a dated copy of the file in it. Walking those reported the
        # breakage at a path inside a backup, which is both wrong and unfixable.
        backups = Path(BACKUP_DIR).expanduser().resolve()
        for folder, subfolders, names in os.walk(base):
            here = Path(folder).resolve()
            if here == backups or backups in here.parents:
                subfolders[:] = []
                continue
            subfolders[:] = [d for d in subfolders
                             if d not in SKIP_FOLDERS and not d.startswith(".")
                             and (here / d).resolve() != backups]
            walked += 1
            if walked > MAX_WALKED_DIRS:
                return files
            for name in names:
                if Path(name).suffix.lower() not in CODE_SUFFIXES:
                    continue
                path = Path(folder) / name
                if path in seen or is_sensitive(str(path)):
                    continue
                seen.add(path)
                files.append(path)
                if len(files) >= MAX_INDEXED_FILES:
                    return files
    return files


def find_references(symbol, roots, skip=None):
    """Where `symbol` is defined and used across `roots`.

    Returns (definitions, uses) as lists of "path:line" strings."""
    symbol = (symbol or "").strip()
    definitions, uses = [], []
    if not symbol or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
        return definitions, uses
    word = re.compile(rf"\b{re.escape(symbol)}\b")
    for path in code_files(roots):
        if skip is not None and path == skip:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not word.search(text):
            continue            # cheap reject before parsing anything
        for line, defines in symbol_occurrences(path, text).get(symbol, []):
            (definitions if defines else uses).append(f"{path}:{line}")
            if len(definitions) + len(uses) >= MAX_REFERENCE_HITS:
                return definitions, uses
    return definitions, uses


def defined_symbols(path, text):
    """Names this file introduces - the things other files could be depending on."""
    # Two characters, not three: `uv` is the most common varying in any shader, and
    # `go`, `id` and `dt` are ordinary function and field names. Single letters are
    # left out because they are loop counters.
    return {name for name, spots in symbol_occurrences(path, text).items()
            if len(name) >= 2 and any(defines for _, defines in spots)}


def broken_references(path, before, after, roots):
    """Definitions this change removed that other files still refer to."""
    if not before or before == after:
        return ""
    lost = defined_symbols(path, before) - defined_symbols(path, after)
    if not lost:
        return ""
    nearby = [project_root(path, roots)]
    casualties = []
    for name in sorted(lost):
        _, uses = find_references(name, nearby, skip=Path(path))
        if uses:
            casualties.append((name, uses))
        if len(casualties) >= 4:
            break
    if not casualties:
        return ""
    lines = "; ".join(f"'{name}' is still used at {', '.join(spots[:3])}"
                      for name, spots in casualties)
    return (f" WARNING: that removed definitions other files still reference - {lines}. "
            "Either put them back or update those files too, or they are now broken.")


def describe_references(raw):
    """USAGES: <symbol> - where a name is defined and used across trusted folders."""
    symbol = (raw or "").split("|")[0].strip()
    if not symbol:
        return "[USAGES needs a name, e.g. USAGES: texcoord]"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
        return (f"[USAGES takes one plain identifier, not '{symbol}'. Use FIND for "
                "free text or file patterns.]")
    roots = load_trusted()
    if not roots:
        return ("[No trusted folders configured, so there is nothing to search. Add the "
                "project folder in the sidebar first.]")
    definitions, uses = find_references(symbol, roots)
    if not definitions and not uses:
        return (f"'{symbol}' does not appear anywhere in your trusted folders. If you "
                "expected it to, check the spelling or the folder.")
    parts = [f"'{symbol}':"]
    parts.append("  defined at: " + (", ".join(definitions[:10]) if definitions
                                     else "nowhere found - it may come from a library"))
    if uses:
        parts.append(f"  used at ({len(uses)}): " + ", ".join(uses[:20]))
        parts.append("  Changing or removing the definition breaks every one of those, "
                     "so update them in the same turn.")
    else:
        parts.append("  used nowhere else, so changing it is safe.")
    return "\n".join(parts)


UNBOUNDED = 99


TS_FUNCTION_NODES = ("function_definition", "function_declaration", "function_item",
                     "method_definition", "method_declaration")


TS_OPTIONAL_PARAMS = ("default_parameter", "optional_parameter",
                      "optional_parameter_declaration", "assignment_pattern")


TS_REST_PARAMS = ("rest_parameter", "rest_pattern", "variadic_parameter",
                  "variadic_declarator", "spread_element", "vararg_expression",
                  "variadic_parameter_declaration")


# TypeScript calls every parameter `required_parameter`, defaults and rest included,
# so counting them would report a default-valued argument as mandatory and invent
# callers to blame. Syntax checks and reference tracking still cover these files.
NO_ARITY_LANGUAGES = {"typescript", "tsx"}


TS_SKIP_ARGS = ("spread_element", "comment")


def _python_arity(text):
    """{name: (min_args, max_args, is_method)} using the real grammar."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {}
    found = {}

    def visit(node, in_class):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, True)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                spec = child.args
                positional = list(spec.posonlyargs) + list(spec.args)
                method = bool(in_class and positional
                              and positional[0].arg in ("self", "cls"))
                low = len(positional) - len(spec.defaults)
                high = len(positional)
                if spec.vararg or spec.kwarg or spec.kwonlyargs:
                    high = UNBOUNDED
                found.setdefault(child.name, []).append((max(low, 0), high, method))
                visit(child, False)
            else:
                visit(child, in_class)

    visit(tree, False)
    # A name defined twice is an overload, a conditional definition or a same-named
    # method on two classes. Nothing here can tell which call meant which.
    return {name: specs[0] for name, specs in found.items() if len(specs) == 1}


def _python_calls(text):
    """[(name, line, argument_count, called_on_an_object)]"""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            continue                     # f(*args) - the count is unknowable
        if any(word.arg is None for word in node.keywords):
            continue                     # f(**kwargs), likewise
        if isinstance(node.func, ast.Name):
            name, attribute = node.func.id, False
        elif isinstance(node.func, ast.Attribute):
            name, attribute = node.func.attr, True
        else:
            continue
        calls.append((name, node.lineno, len(node.args) + len(node.keywords), attribute))
    return calls


def _ts_named(node):
    return [child for child in node.children
            if child.is_named and child.type not in ("comment",)]


def _ts_function_parts(node):
    """(name node, parameter list node) for a definition, in either shape.

    C and GLSL hang both off a function_declarator; JavaScript and GDScript put them
    directly on the definition."""
    name = node.child_by_field_name("name")
    params = node.child_by_field_name("parameters")
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        name = name or declarator.child_by_field_name("declarator")
        params = params or declarator.child_by_field_name("parameters")
    return name, params


def _ts_arity(path, text):
    if ts_language(path) in NO_ARITY_LANGUAGES:
        return {}
    tree = ts_parse(path, text)
    if tree is None:
        return {}
    found, stack = {}, [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in TS_FUNCTION_NODES:
            continue
        name_node, params = _ts_function_parts(node)
        if name_node is None or params is None:
            continue
        name = text[name_node.start_byte:name_node.end_byte].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        entries = _ts_named(params)
        if any(entry.type in TS_REST_PARAMS for entry in entries):
            low = high = UNBOUNDED
        else:
            optional = sum(1 for e in entries if e.type in TS_OPTIONAL_PARAMS)
            low, high = len(entries) - optional, len(entries)
        found.setdefault(name, []).append((low, high, False))
    return {name: specs[0] for name, specs in found.items() if len(specs) == 1}


def _ts_calls(path, text):
    tree = ts_parse(path, text)
    if tree is None:
        return []
    calls, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in ("call_expression", "call"):
            continue
        args = node.child_by_field_name("arguments")
        target = node.child_by_field_name("function")
        if target is None:
            named = _ts_named(node)
            target = named[0] if named else None
        if args is None or target is None:
            continue
        name = text[target.start_byte:target.end_byte].strip()
        # Only plain calls. A member call needs to know which object it is on, and
        # guessing produces confident nonsense.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        entries = _ts_named(args)
        if any(entry.type in TS_SKIP_ARGS for entry in entries):
            continue
        calls.append((name, node.start_point[0] + 1, len(entries), False))
    return calls


def function_arity(path, text):
    if Path(path).suffix.lower() in (".py", ".pyw"):
        return _python_arity(text)
    return _ts_arity(path, text)


def function_calls(path, text):
    if Path(path).suffix.lower() in (".py", ".pyw"):
        return _python_calls(text)
    return _ts_calls(path, text)


def _accepts(count, low, high):
    return low <= count <= high


def signature_breakage(path, before, after, roots):
    """Call sites this change invalidated by altering a function's parameters."""
    if not before or before == after:
        return ""
    was = function_arity(path, before)
    now = function_arity(path, after)
    changed = {}
    for name, (low, high, method) in now.items():
        old = was.get(name)
        if old and (old[0], old[1]) != (low, high) and UNBOUNDED not in (old[1], high):
            changed[name] = (old, (low, high, method))
    if not changed:
        return ""

    path = Path(path)
    hits = []
    for source in code_files([project_root(path, roots)]):
        try:
            text = after if source == path else source.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(re.search(rf"\b{re.escape(name)}\b", text) for name in changed):
            continue
        for name, line, count, attribute in function_calls(source, text):
            spec = changed.get(name)
            if spec is None:
                continue
            (old_low, old_high, _), (new_low, new_high, method) = spec
            if attribute != method:
                continue          # a method call needs the object; a plain one doesn't
            shift = 1 if method else 0    # `self` is never passed at the call site
            if (_accepts(count + shift, old_low, old_high)
                    and not _accepts(count + shift, new_low, new_high)):
                wants = (f"{new_low - shift}" if new_low == new_high
                         else f"{new_low - shift} to {new_high - shift}")
                hits.append(f"{source}:{line} calls {name}({count} arg"
                            f"{'' if count == 1 else 's'}) but it now takes {wants}")
        if len(hits) >= 6:
            break
    if not hits:
        return ""
    return (" WARNING: that changed a function's parameters and left callers behind - "
            + "; ".join(hits[:6]) + ". Update them, or those files are broken now.")


# Scaffolding from a tool call that should never reach a file. Seen for real: a .gd
# file whose first line was "content=" and which carried a bare ||| with the function
# it was meant to replace duplicated around it. It sat broken in the project for five
# days, because a file that exists and is the right size looks finished.
MANGLED_FIRST_LINE = re.compile(r"^\s*(content|path|old_text|new_text|text)\s*=\s*$")


def mangled_payload(content):
    """Why this content looks like a tool call that came apart, or ""."""
    lines = (content or "").splitlines()
    if lines and MANGLED_FIRST_LINE.match(lines[0]):
        return (f"its first line is {lines[0].strip()!r}, the name of a tool argument "
                "rather than anything belonging in the file")
    for number, line in enumerate(lines, 1):
        if line.strip() == "|||":
            return (f"line {number} is the EDIT separator '|||' on its own, so this is "
                    "an EDIT payload sent to WRITE - the text to find, the separator "
                    "and the replacement would all land in the file")
        if RESULT_ECHO_HEADER_RE.match(line):
            return (f"line {number} is a tool-result header, which is something the app "
                    "writes to you and never something that belongs in a file")
    return ""


def do_write(raw, content):
    path, err = resolve_guarded(raw)
    if err:
        return err
    if path in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    if not path.parent.exists():
        return f"[Refused: parent folder missing: {path.parent}. MKDIR it first.]"
    if not (content or "").strip():
        # An empty WRITE is the model failing to produce the body, not a request for an
        # empty file - and it leaves a 0-byte file that looks like finished work.
        return ("[Refused: no content to write. Send the file's actual contents with "
                "the call; an empty file is not work.]")
    broken = mangled_payload(content)
    if broken:
        # Refused rather than written-with-a-warning: the file on disk is still right,
        # and the call can simply be made again. A corrupted file of plausible size is
        # the far harder thing to notice.
        return ("[Refused: that is a malformed tool call, not file contents - "
                f"{broken}. NOTHING was written. Send just the text of the file.]")
    previous = path.stat().st_size if path.is_file() else 0
    was = None
    if path.is_file():
        try:
            was = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            was = None
    backup = _backup(path)
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"[Write failed: {exc}]"
    note = f" Previous version backed up to '{backup}'." if backup else ""
    if previous >= 200 and len(content) < previous / 2:
        note += (f" WARNING: that replaced {previous} bytes with {len(content)} characters, "
                 "so most of the file is gone. If you meant to change part of it, restore "
                 "from the backup above and use EDIT instead of WRITE.")
    note += syntax_warning(path, content)
    # tree-sitter only ever reports what this change introduced, so it needs the old
    # text. A brand new file has no baseline and is left to the exact checkers above.
    if was is not None:
        introduced = introduced_syntax_error(path, was, content)
        if introduced:
            note += (f" WARNING: this rewrite introduced a {introduced}, which was not "
                     "there before. Fix it before going on.")
        trusted = load_trusted()
        note += broken_references(path, was, content, trusted)
        note += signature_breakage(path, was, content, trusted)
    return f"Wrote {len(content)} characters to '{path}'.{note}"


# Separates the text to find from its replacement in an EDIT payload. Canonically '|||'
# alone on its own line: inline, there is no way to tell the separator's own spacing from
# the indentation of the code beside it, and guessing wrong dedents the replacement.
EDIT_SEP = "\n|||\n"


_SEP_TOKEN = r"(?:\|\|\||<<<\s*(?:REPLACE\s*WITH|NEW|WITH)\s*>>>)"


EDIT_SEP_RE = re.compile(
    # Own line - the unambiguous form, so indentation on both sides survives intact.
    rf"\n[ \t]*{_SEP_TOKEN}[ \t]*(?:\n|$)"
    # Inline, for a one-line edit: a single space either side is delimiter, not content.
    rf"|[ \t]?{_SEP_TOKEN}[ \t]?", re.I)


def _reindent(text, removed, added):
    """Swap one line-leading indent prefix for another across a block."""
    if removed == added:
        return text
    lines = text.split("\n")
    return "\n".join(
        (added + line[len(removed):]) if line.startswith(removed) and line.strip()
        else line for line in lines)


def whitespace_variants(needle, replacement):
    """Near-misses of `needle` that differ only in leading indentation.

    Yields (needle, replacement) pairs with the same shift applied to both, so a
    replacement never lands at a different indent from the text it replaces."""
    first = needle.split("\n", 1)[0]
    indent = first[:len(first) - len(first.lstrip(" \t"))]
    if indent:
        # The indent the model supplied is wrong - try without it, and try it as one
        # level less, which is what an off-by-one ' | ' delimiter looks like.
        yield _reindent(needle, indent, ""), _reindent(replacement, indent, "")
        for trimmed in (indent[1:], indent[:-1]):
            if trimmed != indent:
                yield (_reindent(needle, indent, trimmed),
                       _reindent(replacement, indent, trimmed))
    else:
        # Or the model dropped indentation the file actually has.
        for guess in ("    ", "\t", "  ", "        "):
            yield _reindent(needle, "", guess), _reindent(replacement, "", guess)


def do_edit(raw, payload):
    """EDIT | path | old text ||| new text - replace one exact passage in a file.

    Whole-file WRITE is how a small model destroys a file it only meant to touch: it
    has to reproduce every line it isn't changing, and it won't. This changes only the
    passage it names, and refuses unless that passage appears exactly once, so a
    mismatch is a no-op with an explanation rather than a mangled file."""
    path, err = resolve_guarded(raw)
    if err:
        return err
    if path in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    if not path.exists():
        return f"[Not found: {path}. Use FILE_OP WRITE to create a new file.]"
    if not path.is_file():
        return f"[Not a regular file: {path}]"
    if path.stat().st_size > MAX_READ_BYTES:
        return f"[File too large to edit: {path.stat().st_size} bytes]"

    parts = EDIT_SEP_RE.split(payload, maxsplit=1)
    if len(parts) < 2:
        return ("[EDIT needs a line containing only ||| between the text to find and its "
                "replacement: FILE_OP: EDIT | path | old text, newline, |||, newline, "
                "new text.]")
    old_text, new_text = parts[0], parts[1]
    if not old_text:
        return ("[EDIT needs some text to find. To create a file or replace all of it, "
                "use WRITE instead.]")

    try:
        with open(path, encoding="utf-8", newline="") as handle:
            original = handle.read()
    except Exception as exc:
        return f"[Read failed: {exc}]"

    # newline="" keeps the file's own line endings, so editing two lines of a CRLF file
    # doesn't silently rewrite every line in it.
    needle, replacement = old_text, new_text
    if "\r\n" in original and "\r\n" not in needle:
        needle = needle.replace("\n", "\r\n")
        replacement = replacement.replace("\n", "\r\n")

    found = original.count(needle)
    if found == 0:
        # Watched three times in a row: the text to find is right except for a space or
        # two of leading indentation the model added that is not in the file. Exactness
        # is worth keeping - it is what stops an edit landing in the wrong place - so
        # rather than matching loosely, look for one unambiguous near-miss and edit
        # THAT literal text. Anything that matches more than once is still refused.
        for shift in whitespace_variants(needle, replacement):
            shifted_needle, shifted_replacement = shift
            if original.count(shifted_needle) == 1:
                needle, replacement = shifted_needle, shifted_replacement
                found = 1
                break
    if found == 0:
        body = [line for line in old_text.splitlines() if line.strip()]
        if body and all(NUMBERED_LINE_RE.match(line) for line in body):
            return (f"[No match in {path.name}: the text to find still has READ_FILE's line "
                    "numbers attached. They are display only - copy just the text that "
                    "follows the '|'.]")
        return (f"[No match in {path.name}. The text to find must match the file exactly, "
                "including indentation. READ_FILE it and copy the lines verbatim rather "
                "than retyping them.]")
    if found > 1:
        return (f"[Found {found} matches in {path.name}, so this edit is ambiguous and was "
                "not applied. Include more surrounding lines so the text to find appears "
                "exactly once.]")

    line_no = original[:original.index(needle)].count("\n") + 1
    backup = _backup(path)
    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(original.replace(needle, replacement, 1))
    except Exception as exc:
        return f"[Edit failed: {exc}]"
    note = f" Previous version backed up to '{backup}'." if backup else ""
    if needle != old_text and needle != old_text.replace("\n", "\r\n"):
        note += (" (The text to find did not match exactly - its indentation differed - "
                 "so the matching text in the file was used instead. Copy from READ_FILE "
                 "verbatim to avoid this.)")
    # An edit is as capable of breaking a file as a write, and more likely to: it is
    # splicing into code it did not just compose.
    updated = original.replace(needle, replacement, 1)
    note += syntax_warning(path, updated)
    introduced = introduced_syntax_error(path, original, updated)
    if introduced:
        note += (f" WARNING: this edit introduced a {introduced}, which was not there "
                 "before. Fix it before going on.")
    trusted = load_trusted()
    note += broken_references(path, original, updated, trusted)
    note += signature_breakage(path, original, updated, trusted)
    return (f"Edited '{path}' at line {line_no}: replaced {old_text.count(chr(10)) + 1} "
            f"line(s) with {new_text.count(chr(10)) + 1}.{note}")


# action -> (function, needs_two_args, human-readable confirmation template)
FILE_OPS = {
    "RENAME": (do_rename, True, "Rename file/folder:\n  {a}\n  -> {b}"),
    "MOVE": (do_move, True, "Move:\n  {a}\n  -> {b}"),
    "COPY": (do_copy, True, "Copy:\n  {a}\n  -> {b}"),
    "DELETE": (do_delete, False, "DELETE (cannot be undone):\n  {a}"),
    "MKDIR": (do_mkdir, False, "Create folder:\n  {a}"),
    "WRITE": (do_write, True, "Write whole file:\n  {a}\n\nContent preview:\n{b}"),
    "EDIT": (do_edit, True, "Edit part of file:\n  {a}\n\nChange:\n{b}"),
}
