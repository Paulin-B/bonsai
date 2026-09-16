"""Load the application for the tests, independent of the working directory.

Tests import the package rather than running it, so they can exercise the pure
functions and the Qt widgets without a model server or a compositor. `load()` returns
the package itself, which re-exports every module - so a test can reach any name
without caring which module it ended up in.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Some tests read the source to check a rule holds everywhere rather than in one spot
# (for example, that nothing dispatches unattended by accident). APP is the whole
# package, concatenated in import order.
_PACKAGE = ROOT / "bonsai"
_ORDER = ["config", "theme", "store", "text", "files", "codeintel", "media", "shell", "turns", "chats", "prompt", "worker", "widgets", "app"]


class _Source:
    """Stands in for the old single-file path object."""

    def read_text(self, *args, **kwargs):
        return "\n".join((_PACKAGE / f"{name}.py").read_text()
                         for name in _ORDER)

    def __fspath__(self):
        return str(_PACKAGE)


APP = _Source()


def load():
    import bonsai
    return bonsai
