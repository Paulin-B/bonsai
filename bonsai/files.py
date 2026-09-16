"""Reading, finding and changing files, with the guards that keep it safe."""

import errno
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from .config import (
    BACKUP_DIR, DEFAULTS, MAX_READ_BYTES, PROTECTED_PATHS, SEARCH_MIN_INTERVAL, SENSITIVE_PATTERNS,
)
from .store import (
    load_trusted, settings,
)


def is_sensitive(path):
    return any(p in str(path).lower() for p in SENSITIVE_PATTERNS)


def resolve_guarded(raw):
    """Resolve a model-supplied path. Returns (Path, None) or (None, error)."""
    try:
        path = Path(raw).expanduser().resolve()
    except Exception as exc:
        return None, f"[Invalid path: {exc}]"
    if is_sensitive(path):
        return None, "[Refused: path matches a sensitive/credential file pattern.]"
    return path, None


def path_is_trusted(raw, roots):
    try:
        path = Path(raw).expanduser().resolve()
    except Exception:
        return False
    for root in roots:
        try:
            root_path = Path(root).expanduser().resolve()
        except Exception:
            continue
        if path == root_path or root_path in path.parents:
            return True
    return False


_last_search = 0.0


_search_cache = {}


def search_web(query, limit=5):
    global _last_search
    config = settings()
    if not config.get("web_search_enabled", True):
        return "[Web search is off. Answer from what you know and say you couldn't search.]"

    # Models often wrap the query in quotes; searching for '"omarchy"' literally
    # is a different (worse) query than searching for omarchy.
    query = query.strip().strip('"\'').strip()
    key = query.lower()
    if key in _search_cache:
        return _search_cache[key]

    wait = SEARCH_MIN_INTERVAL - (time.time() - _last_search)
    if wait > 0:
        time.sleep(wait)  # engines CAPTCHA-block bursts; suspensions last an hour
    _last_search = time.time()

    try:
        response = requests.get(
            config.get("searxng_url", DEFAULTS["searxng_url"]),
            params={"q": query, "format": "json"},
            headers={"User-Agent": "Mozilla/5.0 (Bonsai/1.0)"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 403:
            return ("[Search failed: 403 from searxng - its limiter is blocking us. "
                    "Set 'limiter: false' in searxng/settings.yml.]")
        return f"[Search failed: {exc}]"
    except Exception as exc:
        return f"[Search failed: {exc}. Is the searxng container running?]"

    results = data.get("results", [])[:limit]
    if not results:
        dead = data.get("unresponsive_engines") or []
        if dead:
            reasons = ", ".join(f"{n}: {r}" for n, r in dead)
            return (f"[No results - all engines unavailable ({reasons}). This is rate limiting "
                    "on the engines, not the query. Do NOT retry or reword; tell the user search "
                    "is temporarily blocked and answer from what you know.]")
        return "[No search results found.]"

    lines = [
        f"- {r.get('title', '').strip()}\n  {r.get('url', '').strip()}\n  {r.get('content', '').strip()}"
        for r in results
    ]
    _search_cache[key] = "\n".join(lines)
    return _search_cache[key]


RASTER_FORMATS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}


MIN_IMAGE_PIXELS = 640


def search_images(query, limit=8):
    """SEARCH_IMAGES: <query> - find pictures, returning direct image URLs.

    Filtered to raster images of a usable size: a plain image search returns icon SVGs
    and stock thumbnails alongside the real results, and neither is any use in a
    document."""
    config = settings()
    if not config.get("web_search_enabled", True):
        return "[Web search is off, so image search is unavailable.]"
    query = (query or "").strip().strip("\"'")
    if not query:
        return "[SEARCH_IMAGES needs something to look for.]"
    try:
        response = requests.get(
            config.get("searxng_url", DEFAULTS["searxng_url"]),
            params={"q": query, "format": "json", "categories": "images"},
            headers={"User-Agent": "Mozilla/5.0 (Bonsai/1.0)"}, timeout=20)
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception as exc:
        return f"[Image search failed: {exc}. Is the searxng container running?]"

    lines = []
    for result in results:
        source = (result.get("img_src") or "").strip()
        if not source.startswith("http"):
            continue
        suffix = Path(source.split("?")[0]).suffix.lstrip(".").lower()
        declared = (result.get("img_format") or "").lower()
        if suffix and suffix not in RASTER_FORMATS:
            continue
        if declared and declared not in RASTER_FORMATS:
            continue
        size = str(result.get("resolution") or "")
        digits = [int(n) for n in re.findall(r"\d+", size)]
        if digits and max(digits) < MIN_IMAGE_PIXELS:
            continue
        title = " ".join((result.get("title") or "image").split())[:70]
        lines.append(f"- {title} [{size or 'size unknown'}]\n  {source}")
        if len(lines) >= limit:
            break
    if not lines:
        return f"[No usable images found for '{query}'.]"
    return ("Images found (DOWNLOAD one to a file before putting it in a document):\n"
            + "\n".join(lines))


URL_RE = re.compile(r"https?://[^\s\]<>\"'|]+")


def clean_url(raw):
    """Models often decorate a URL with a title or markdown, e.g.
    'https://x.com/a -> Some Page Title' or '[Docs](https://x.com/a)'.
    Pull out the actual URL rather than sending the decoration to requests."""
    match = URL_RE.search(raw or "")
    if not match:
        return None
    return match.group(0).rstrip(".,;:)\u2019\"'")


def fetch_url(url):
    url = clean_url(url)
    if not url:
        return "[FETCH needs an http(s) URL.]"
    limit = settings().get("max_fetch_chars", 6000)
    try:
        response = requests.get(url, timeout=15,
                                headers={"User-Agent": "Mozilla/5.0 (Bonsai/1.0)"})
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return (f"[404 Not Found: {url}. Don't guess documentation URLs - FETCH the "
                    "site's index or search results page and use a link that actually "
                    "appears in it.]")
        return f"[Fetch failed: {exc}]"
    except Exception as exc:
        return f"[Fetch failed: {exc}]"
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())
    if not text:
        return "[No readable text on page.]"
    return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")


READ_WINDOW = 400           # lines returned when no range is asked for


MAX_READ_LINES = 1200       # ceiling on an explicitly requested range


RANGE_RE = re.compile(r"^(\d+)\s*(?:-\s*(\d+)?)?$")


NUMBERED_LINE_RE = re.compile(r"^\s*\d+\|")


def read_file(raw, numbered=True):
    """READ_FILE: <path> [| <first>-<last>] - read a text file.

    Output is line-numbered so a long file can be navigated by number instead of
    guessed at, and truncation names the lines it left out along with the call that
    fetches them. A bare '...[truncated]' let the model believe it had seen the whole
    file, and EDIT then failed trying to match text it was never shown."""
    limit = settings().get("max_read_chars", 8000)
    parts = [part.strip() for part in str(raw).split("|", 1)]
    range_raw = parts[1] if len(parts) > 1 else ""

    path, err = resolve_guarded(parts[0])
    if err:
        return err
    if not path.exists():
        return f"[File not found: {path}]"
    if not path.is_file():
        return f"[Not a regular file: {path}]"
    if path.stat().st_size > MAX_READ_BYTES:
        return f"[File too large: {path.stat().st_size} bytes]"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[Read failed: {exc}]"

    if not numbered:
        # Attached files are raw content the model may be asked to write back out, so
        # they must not carry line numbers into the file.
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n...[truncated: {limit} of {len(text)} characters shown]"

    lines = text.splitlines()
    total = len(lines)
    if not total:
        return f"(empty file: {path})"

    # A fixed window by default, so "read the next 400" is a move the model can plan;
    # the character cap below is only a backstop for pathologically long lines.
    start, end = 1, READ_WINDOW
    if range_raw:
        match = RANGE_RE.match(range_raw)
        if not match:
            return (f"[Couldn't read '{range_raw}' as a line range. Use "
                    "READ_FILE: <path> | <first>-<last>, for example | 120-260.]")
        start = max(1, int(match.group(1)))
        if start > total:
            return f"[{path.name} has only {total} lines, so line {start} is past the end.]"
        end = int(match.group(2)) if match.group(2) else start + READ_WINDOW - 1
    end = min(end, total, start + MAX_READ_LINES - 1)

    width = len(str(end))
    shown, used, last = [], 0, start - 1
    for offset, line in enumerate(lines[start - 1:end]):
        number = start + offset
        rendered = f"{number:>{width}}| {line}"
        if shown and used + len(rendered) + 1 > limit:
            break       # keep at least one line, even a pathologically long one
        shown.append(rendered)
        used += len(rendered) + 1
        last = number

    out = [f"{path} (lines {start}-{last} of {total}):"] + shown
    if last < total:
        following = min(total, last + READ_WINDOW)
        out.append(f"...[{total - last} more lines. Read them with "
                   f"READ_FILE: {path} | {last + 1}-{following}]")
    out.append("(Line numbers are for reference only - never copy them into an EDIT.)")
    return "\n".join(out)


def list_directory(raw, limit=200):
    """Lists a folder and one level inside each subfolder. A flat listing made the
    model conclude a file was missing when it was one directory down."""
    path, err = resolve_guarded(raw)
    if err:
        return err
    if not path.exists():
        return f"[Directory not found: {path}]"
    if not path.is_dir():
        return f"[Not a directory: {path}]"
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception as exc:
        return f"[Listing failed: {exc}]"
    if not entries:
        return f"(empty directory: {path})"

    lines, shown = [], 0
    for entry in entries:
        if shown >= limit:
            break
        if entry.is_dir():
            lines.append(f"[DIR]  {entry.name}/")
            shown += 1
            try:
                children = sorted(entry.iterdir(),
                                  key=lambda p: (not p.is_dir(), p.name.lower()))
            except Exception:
                continue
            for child in children[:20]:
                if shown >= limit:
                    break
                mark = "/" if child.is_dir() else ""
                size = "" if child.is_dir() else f" ({child.stat().st_size} bytes)"
                lines.append(f"         {entry.name}/{child.name}{mark}{size}")
                shown += 1
            if len(children) > 20:
                lines.append(f"         ...[{len(children) - 20} more in {entry.name}/]")
        else:
            lines.append(f"[FILE] {entry.name} ({entry.stat().st_size} bytes)")
            shown += 1

    out = f"Contents of {path} (one level deep):\n" + "\n".join(lines)
    if shown >= limit:
        out += "\n...[truncated - use FIND to locate a specific file]"
    return out


def find_files(raw, limit=60):
    """FIND: <name or pattern> | <root folder>. Answers 'where is that file'
    without the model guessing directory names."""
    parts = [p.strip() for p in raw.split("|", 1)]
    pattern = parts[0]
    root_raw = parts[1] if len(parts) > 1 else ""
    if not pattern:
        return "[FIND needs a filename or pattern, e.g. FIND: player.gd | ~/Projects]"

    if root_raw:
        root, err = resolve_guarded(root_raw)
        if err:
            return err
    else:
        trusted = load_trusted()
        if not trusted:
            return "[FIND needs a folder to search in: FIND: <pattern> | <folder>]"
        root = Path(trusted[0])
    if not root.is_dir():
        return f"[Not a directory: {root}]"

    if not any(ch in pattern for ch in "*?["):
        pattern = f"*{pattern}*"
    try:
        hits = []
        for found in root.rglob(pattern):
            if is_sensitive(found):
                continue
            hits.append(found)
            if len(hits) >= limit:
                break
    except Exception as exc:
        return f"[Search failed: {exc}]"
    if not hits:
        return f"[Nothing matching '{pattern}' under {root}.]"
    lines = [f"{h} ({h.stat().st_size} bytes)" if h.is_file() else f"{h}/" for h in hits]
    return f"Matches for '{pattern}' under {root}:\n" + "\n".join(lines)


def _dest_path(source, raw_dest):
    """A destination with no directory means 'same folder, new name'. Resolving it
    against the process CWD instead would cross filesystems and fail with EXDEV."""
    dest = Path(raw_dest).expanduser()
    return dest if dest.is_absolute() else source.parent / dest.name


def do_rename(old_raw, new_raw):
    old, err = resolve_guarded(old_raw)
    if err:
        return err
    new, err = resolve_guarded(str(_dest_path(old, new_raw)))
    if err:
        return err
    if old in PROTECTED_PATHS or new in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    if not old.exists():
        return f"[Not found: {old}]"
    if new.exists():
        return f"[Refused: destination exists: {new}]"
    try:
        old.rename(new)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            try:
                shutil.move(str(old), str(new))
            except Exception as exc2:
                return f"[Rename failed: {exc2}]"
        else:
            return f"[Rename failed: {exc}]"
    return f"Renamed '{old}' to '{new}'."


def do_move(src_raw, dst_raw):
    src, err = resolve_guarded(src_raw)
    if err:
        return err
    dst, err = resolve_guarded(dst_raw)
    if err:
        return err
    if src in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    if not src.exists():
        return f"[Not found: {src}]"
    try:
        shutil.move(str(src), str(dst))
    except Exception as exc:
        return f"[Move failed: {exc}]"
    return f"Moved '{src}' to '{dst}'."


def do_copy(src_raw, dst_raw):
    src, err = resolve_guarded(src_raw)
    if err:
        return err
    dst, err = resolve_guarded(dst_raw)
    if err:
        return err
    if not src.exists():
        return f"[Not found: {src}]"
    if dst.exists():
        return f"[Refused: destination exists: {dst}]"
    try:
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
    except Exception as exc:
        return f"[Copy failed: {exc}]"
    return f"Copied '{src}' to '{dst}'."


def do_delete(raw):
    path, err = resolve_guarded(raw)
    if err:
        return err
    if path in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    if not path.exists():
        return f"[Not found: {path}]"
    backup = _backup(path)
    try:
        if path.is_dir():
            shutil.rmtree(str(path))
        else:
            path.unlink()
    except Exception as exc:
        return f"[Delete failed: {exc}]"
    note = f" A copy was kept at '{backup}'." if backup else ""
    return f"Deleted '{path}'.{note}"


def do_mkdir(raw):
    path, err = resolve_guarded(raw)
    if err:
        return err
    if path in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    existed = path.exists()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return f"[Mkdir failed: {exc}]"
    return f"Folder '{path}' already existed." if existed else f"Created folder '{path}'."


def _backup(path):
    """Copy a file into the backup folder before it is overwritten or deleted.
    Parser bugs and bad model output have destroyed files here before; recovery
    should not depend on the model having been careful."""
    if not path.exists() or not path.is_file():
        return None
    # Milliseconds, because two writes to the same file inside one second used to
    # land in one folder and the second backup overwrote the first.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    target_dir = BACKUP_DIR / stamp
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        # Timestamp precision alone is not enough - two writes to one file can land in
        # the same millisecond, and the second backup would overwrite the first.
        attempt = 2
        while target.exists():
            target = target_dir / f"{path.stem}.{attempt}{path.suffix}"
            attempt += 1
        shutil.copy2(str(path), str(target))
    except Exception:
        return None  # a failed backup must not block the operation
    _prune_backups()
    return target


def _prune_backups(keep=200):
    try:
        folders = sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir())
        for old in folders[:-keep]:
            shutil.rmtree(str(old), ignore_errors=True)
    except Exception:
        pass


def name_inside(path, title, fallback, suffix):
    """Turn a folder into a file inside it, named after the title.

    "Put a chart in <folder>" hands over a folder, and with_suffix() would then write
    a sibling file *beside* it - work/ becomes work.png - which is not where the user
    looked for it and not what the reply claimed."""
    if not path.is_dir():
        return path
    stem = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    return path / ((stem[:60] or fallback) + suffix)
