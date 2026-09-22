"""Bonsai - a local AI assistant that watches, acts and remembers.

Everything is re-exported here, so `from bonsai import x` works regardless of
which module x lives in. Import from the modules directly if you prefer."""

from . import config  # noqa: F401
from . import theme  # noqa: F401
from . import store  # noqa: F401
from . import text  # noqa: F401
from . import files  # noqa: F401
from . import codeintel  # noqa: F401
from . import media  # noqa: F401
from . import shell  # noqa: F401
from . import stage  # noqa: F401
from . import speech  # noqa: F401
from . import listen  # noqa: F401
from . import hotkey  # noqa: F401
from . import avatar  # noqa: F401
from . import neuro  # noqa: F401
from . import turns  # noqa: F401
from . import chats  # noqa: F401
from . import prompt  # noqa: F401
from . import worker  # noqa: F401
from . import widgets  # noqa: F401
from . import app  # noqa: F401

from .config import *  # noqa: F401,F403
from .theme import *  # noqa: F401,F403
from .store import *  # noqa: F401,F403
from .text import *  # noqa: F401,F403
from .files import *  # noqa: F401,F403
from .codeintel import *  # noqa: F401,F403
from .media import *  # noqa: F401,F403
from .shell import *  # noqa: F401,F403
from .stage import *  # noqa: F401,F403
from .speech import *  # noqa: F401,F403
from .listen import *  # noqa: F401,F403
from .hotkey import *  # noqa: F401,F403
from .avatar import *  # noqa: F401,F403
from .neuro import *  # noqa: F401,F403
from .turns import *  # noqa: F401,F403
from .chats import *  # noqa: F401,F403
from .prompt import *  # noqa: F401,F403
from .worker import *  # noqa: F401,F403
from .widgets import *  # noqa: F401,F403
from .app import *  # noqa: F401,F403

import sys as _sys
import types as _types

_MODULES = [config, theme, store, text, files, codeintel, media, shell, turns, chats, prompt, worker, widgets, app]

# `import *` skips names that begin with an underscore. They are still part of the
# app - helpers, compiled patterns, the optional spellchecker - and tests reach for
# them by name, so make them visible here too rather than leaving a package that
# re-exports "everything" except the private half.
for _module in _MODULES:
    for _name, _value in vars(_module).items():
        if _name.startswith("_") and not _name.startswith("__"):
            globals().setdefault(_name, _value)


class _Package(_types.ModuleType):
    """A package whose attributes really are the app's.

    Everything is re-exported here, so `bonsai.SETTINGS_FILE = path` reads as though
    it moves that file for the whole application. With a plain `from .config import
    SETTINGS_FILE` in each module it would not: every module keeps its own copy of the
    name, and rebinding this one changes nothing. Assignments are forwarded to each
    module that defines the name, which is what a flat re-export ought to mean - and
    it is how tests point the app at a scratch directory.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for module in _MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


_sys.modules[__name__].__class__ = _Package
