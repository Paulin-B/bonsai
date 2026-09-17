"""Paths, defaults, and the rules about what may run or be touched."""

import re
import os
import sys
from pathlib import Path


try:
    from spellchecker import SpellChecker
    _spell = SpellChecker()
except ImportError:
    _spell = None


# Parsers used to check what gets written. Both are optional: a missing one means that
# file type goes unchecked, never that a write fails.
try:
    import tomllib
except ImportError:
    tomllib = None


try:
    import yaml
except ImportError:
    yaml = None


APP_VERSION = "2026-09-17-stopdocker"


# Where the repo lives, for the optional files that ship beside it.
WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"

REPO_ROOT = Path(__file__).resolve().parent.parent

# All state lives here, as plain JSON, and is safe to hand-edit or delete.
# Override with BONSAI_DATA_DIR to keep separate profiles.
def _default_data_dir():
    """Where this platform expects an application to keep its state."""
    if WINDOWS:
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Bonsai"
    if MACOS:
        return Path.home() / "Library/Application Support/Bonsai"
    return Path.home() / ".local/share"


DATA_DIR = Path(os.environ.get("BONSAI_DATA_DIR") or _default_data_dir()).expanduser()


SETTINGS_FILE = DATA_DIR / "bonsai_settings.json"


CHARACTER_FILE = DATA_DIR / "bonsai_character.json"


MEMORY_FILE = DATA_DIR / "bonsai_memory.json"


SCREEN_LOG_FILE = DATA_DIR / "bonsai_screens.json"


TASKS_FILE = DATA_DIR / "bonsai_tasks.json"


SKILLS_FILE = DATA_DIR / "bonsai_skills.json"


PATTERNS_FILE = DATA_DIR / "bonsai_patterns.json"


TRUSTED_PATHS_FILE = DATA_DIR / "bonsai_trusted_paths.json"


PROJECTS_FILE = DATA_DIR / "bonsai_projects.json"


INTERESTS_FILE = DATA_DIR / "bonsai_interests.json"


BRIEFING_FILE = DATA_DIR / "bonsai_briefing.json"
BRIEFING_SEEN_FILE = DATA_DIR / "bonsai_briefing_seen.json"


CHAT_INDEX_FILE = DATA_DIR / "bonsai_chats_index.json"


CHATS_DIR = DATA_DIR / "bonsai_chats"


DEBUG_LOG_FILE = DATA_DIR / "bonsai_debug.log"


BACKUP_DIR = DATA_DIR / "bonsai_backups"


DEFAULTS = {
    "server_url": "http://localhost:8080/v1/chat/completions",
    "searxng_url": "http://localhost:8081/search",
    # Optional. Point these at your own files if you use them; both features are
    # off until you do. Relative to the repo root by default so a clone works.
    "compose_path": str(REPO_ROOT / "docker-compose.yml"),
    "monitor_script": str(REPO_ROOT / "monitor_stream.fish"),
    "web_search_enabled": True,
    "vision_default": True,
    # Theme by name - see bonsai/theme.py. Light and dark are themes like any other,
    # not a separate switch. dark_mode is only read to migrate a settings file written
    # before that, and is no longer written.
    # Sent with each request. Blank means "whatever the server already has":
    # llama.cpp serves one model and ignores it, while LM Studio and Ollama will
    # load the one named here.
    "model": "",
    # Named servers to switch between from the header. llama.cpp serves one model per
    # process and ignores the model field entirely, so swapping models there means
    # swapping endpoints - a second container on the other GPU, or a bigger model on
    # both. Empty means "just use server_url".
    "endpoints": [],
    "theme": "Midnight",
    "font": "System",
    "font_size": 13,
    # 2048 was roughly 200 lines of code, so writing a file in one FILE_OP meant
    # writing it in pieces. Measured on this machine the model generates ~38 tok/s, so
    # this is ~215s of generation - which is why request_timeout moves with it. Raising
    # one without the other buys a cap that cannot be reached before the request dies.
    "max_tokens": 8192,
    "temperature": 1.0,
    "max_tool_steps": 12,
    "max_history_messages": 20,
    "max_read_chars": 8000,
    "max_fetch_chars": 6000,
    "max_learned_entries": 30,
    "core_memory_cap": 12,
    # Where `npx skills add ...` puts things. Bonsai reads SKILL.md folders
    # from any of these, in addition to bonsai_skills.json.
    "skill_dirs": ["~/.agents/skills", "~/.claude/skills",
                   "~/.config/bonsai/skills"],
    "observer_url": "",            # blank = use server_url
    "proactive_interval": 90,      # seconds between screen checks
    "proactive_cooldown": 600,     # seconds of silence after it speaks
    "proactive_verbose": True,     # log each check, so silence != broken
    "auto_max_rounds": 10,         # continuation rounds before auto mode gives up
    "max_continuations": 3,        # times a turn may resume itself after running out
    "request_timeout": 600,        # must outlast max_tokens at ~38 tok/s, plus prefill
    "enable_thinking": False,      # Gemma 4 reasoning traces eat the token budget
    "native_tools": True,          # use llama.cpp schema-validated tool calling
    # Which compose services to start. An idle container still holds RAM, and a second
    # model server holds VRAM, so anything unused stays stopped.
    "services": {"bonsai-api": True, "searxng": True, "bonsai-observer": False},
    # Project notes live here as plain markdown. Point it at an Obsidian vault and the
    # same files open in Obsidian; leave it blank and notes stay in JSON.
    # Blank keeps notes in JSON under DATA_DIR. Point it at an Obsidian vault (or
    # any folder of markdown) and Bonsai reads and writes the same files Obsidian
    # does - no plugin, no export step.
    "vault_path": "",
    "vault_subfolder": "Bonsai",
    "notes_visible": True,
    "sidebar_visible": True,
    "always_on_top": False,        # the window is a full workspace now, not a panel
    # Screen capture. "all" spans every monitor, which on a multi-head desk squeezes a
    # very wide image into an unreadable strip - hence focusing one output by default.
    "capture_mode": "active-monitor",   # active-monitor | active-window | all
    "capture_max_width": 1920,
    "capture_max_height": 1200,
    "capture_quality": 90,
    # Keep the screenshot in context for every step of a turn. With it attached only
    # to the first step, any turn that called a tool answered with no image left.
    "vision_all_steps": True,
    # Stream the closing reply as it is written. Only that call is streamed: the steps
    # before it are tool selection, where partial text would flash up and be replaced.
    "stream_replies": True,
    # The briefing: what is worth reading, gathered before you ask for it.
    "briefing_enabled": True,
    "briefing_on_open": True,       # show it instead of an empty chat at launch
    "briefing_max_age_hours": 6,    # refetch only when the cache is older than this
    "briefing_per_topic": 4,        # results kept per interest, per kind
    "briefing_time_range": "month", # how far back to look; see BRIEFING_RANGES
    # Which kinds of result to gather. SearXNG categories, so anything it serves works.
    "briefing_kinds": ["news", "videos"],
    # Skip anything a previous briefing already showed you, so a refresh is new
    # material rather than the same headlines with a later timestamp.
    "briefing_no_repeats": True,
    "briefing_english_only": True,  # SearXNG's language= is ignored by the engines
    # Ceiling on the tool results carried forward inside one turn. They used to
    # accumulate without limit: 30 steps x max_read_chars is far past any context
    # window, and llama.cpp responds by silently dropping the oldest content - which
    # is the system prompt, and with it every rule the app relies on.
    "max_result_chars": 40000,
    # Output kept from one RUN. The old hardcoded 6000 cut `pacman -Qu` (12k chars,
    # 311 packages) in half, and RUN has no shell, so the model cannot pipe it through
    # grep to narrow it down - whatever is cut is simply gone.
    "max_command_chars": 15000,
    # Run commands inside bubblewrap: everything read-only except the working folder,
    # credentials masked, no network. Lets it check its own work without the check
    # being able to change anything else.
    "sandbox_commands": True,
    "sandbox_network": False,
    # Choosing a tool wants a steady hand; talking to you does not. At 1.5 the model
    # coin-flipped between reading a file first and guessing at an edit; at 0.5 it made
    # the same choice 5 times out of 5. The closing reply is then regenerated at
    # "temperature" so personality survives the steadiness - which costs one extra
    # generation per turn. Set this equal to "temperature" to skip that.
    "tool_temperature": 0.5,
    # Sampler defaults follow Qwen3's recommendation for Instruct models; the previous
    # values (0.95 / 64 / 1.0) were carried over from Gemma.
    "top_p": 0.8,
    "top_k": 20,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.0,

    "last_chat_id": None,
}


# service name -> (what it is for, whether Bonsai cannot run without it)
DOCKER_SERVICES = {
    "bonsai-api": ("the llama.cpp model server", True),
    "searxng": ("web search backend for the SEARCH tool", False),
    "bonsai-observer": ("a second model server for proactive screen checks", False),
}


# Seed only. Once bonsai_character.json exists it is the sole source of truth -
# edit that file to change the personality; this is never consulted again.
CHARACTER_SEED = {
    "name": "Bonsai",
    "core_traits": ["Curious and sarcastic.",
    "Not a corporate assistant.",
    "Happy go lucky, Interested",
    "A little helper."
    ],
    "learned_traits": [],
    "opinions": [],
    "running_jokes": [],
}


MAX_READ_BYTES = 2_000_000


MAX_DOWNLOAD_BYTES = 100_000_000


COMMAND_TIMEOUT = 120


# Commands that may run without a permission prompt when the working directory is a
# trusted folder. Read-heavy, build/test tooling - nothing that reaches the network,
# escalates privileges, or destroys data.
ALLOWED_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "file", "stat", "find", "tree", "du", "df",
    "grep", "rg", "sed", "awk", "sort", "uniq", "diff",
    "git", "python", "python3", "pip", "pytest", "ruff", "black", "mypy",
    "node", "npm", "npx", "tsc", "eslint", "prettier",
    "cargo", "rustc", "go", "make", "cmake", "gcc", "g++",
    "echo", "pwd", "which", "env", "date", "unzip", "tar",
    "pacman", "checkupdates", "paru", "yay", "uname", "hostnamectl", "lscpu", "lsblk",
    "free", "uptime", "nvidia-smi", "pactl", "journalctl",
}


# Never runs, even with permission - these are how an agent ruins a machine, and no
# plausible coding task needs them from here.
FORBIDDEN_COMMANDS = {
    "sudo", "doas", "su", "rm", "rmdir", "dd", "mkfs", "fdisk", "parted", "shutdown",
    "reboot", "systemctl", "chown", "chmod", "chroot", "mount", "umount", "kill",
    "killall", "pkill", "curl", "wget", "ssh", "scp", "nc", "ncat", "sh", "bash",
    "zsh", "fish", "eval", "exec", "docker", "podman", "crontab", "at",
    # The same jobs under different names on Windows. Listed unconditionally: a name
    # that means nothing on this OS costs nothing to refuse, and a dual-boot user
    # editing a .bat should get the same answer either way.
    "del", "erase", "rd", "format", "diskpart", "reg", "regedit", "powershell",
    "pwsh", "cmd", "wmic", "bcdedit", "vssadmin", "cipher", "takeown", "icacls",
    "net", "sc", "schtasks", "shutdown.exe", "taskkill", "rundll32", "mshta",
    "certutil", "bitsadmin",
}


# Programs on the allow-list whose safety depends on the arguments. Only the querying
# forms skip the permission prompt; a sync or an install still asks, even in a trusted
# folder. Root is unreachable so these would fail anyway, but "it would have failed"
# is not the same as "it was not attempted".
READ_ONLY_FORMS = {
    "pacman": re.compile(r"^-(?:-(?:query|search|info|version|help)\b|[QTVh]|Si|Ss)", re.I),
    "paru": re.compile(r"^-(?:-(?:query|search|info|version|help)\b|[QTVh]|Si|Ss)", re.I),
    "yay": re.compile(r"^-(?:-(?:query|search|info|version|help)\b|[QTVh]|Si|Ss)", re.I),
    "git": re.compile(r"^(?:status|log|diff|show|branch|remote|describe|blame|"
                      r"rev-parse|ls-files|shortlog|config)$", re.I),
}


def is_read_only(program, argv):
    """Whether this invocation of an argument-sensitive program only reads."""
    pattern = READ_ONLY_FORMS.get(program)
    if pattern is None:
        return True                      # not argument-sensitive; the list decided
    first = next((a for a in argv[1:] if not a.startswith("--color")), "")
    return bool(first) and bool(pattern.match(first))


# Directories hidden from a sandboxed command. A read-only bind still READS, so the
# whole filesystem being ro-bound does not protect a token - only a tmpfs over the top
# of it does.
SANDBOX_MASKED = [
    "~/.ssh", "~/.gnupg", "~/.aws", "~/.docker", "~/.netrc", "~/.password-store",
    "~/.config/gh", "~/.config/sops", "~/.cache/huggingface", "~/.local/share",
    "~/.mozilla", "~/.config/BraveSoftware",
]


def sandbox_argv(argv, workdir, allow_network=False):
    """Wrap a command so it can only write where it is working.

    Everything is bound read-only so ordinary tools still function, the working folder
    is bound writable, credential directories are masked with an empty tmpfs, and the
    network is severed unless asked for - a sandbox that can still reach the internet
    does not stop anything reading a file and posting it somewhere."""
    wrapped = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
               "--tmpfs", "/tmp"]
    for raw in SANDBOX_MASKED:
        masked = Path(raw).expanduser()
        if masked.exists():
            wrapped += ["--tmpfs", str(masked)]
    wrapped += ["--bind", str(workdir), str(workdir), "--chdir", str(workdir)]
    if not allow_network:
        wrapped.append("--unshare-net")
    wrapped += ["--die-with-parent", "--"]
    return wrapped + list(argv)


SEARCH_MIN_INTERVAL = 5


PROTECTED_PATHS = {
    Path("/"), Path.home(), Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
    Path("/boot"), Path("/lib"), Path("/lib64"), Path("/proc"), Path("/sys"),
    Path("/root"), Path("/var"), Path("/opt"), Path("/dev"), Path("/media"),
}


# Substring matched against the whole lowercased path. Deliberately specific rather
# than matching a bare "token": that would also block tokenizer.json and any source
# file with "token" in its name, which are ordinary things to want to read.
SENSITIVE_PATTERNS = [
    ".ssh", "id_rsa", "id_ed25519", "id_ecdsa", ".gnupg", ".aws/credentials",
    ".aws/config", "shadow", "gshadow", ".env", "credentials", "cookies.sqlite",
    "login data", "known_hosts", "_history", ".kube/config", ".netrc", "wallet.dat",
    ".password-store", ".pgpass", "secret",
    "huggingface/token", ".hf_token", "hf_token", "access_token", "refresh_token",
    ".git-credentials", ".docker/config.json", ".npmrc", ".pypirc",
]
