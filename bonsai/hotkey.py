"""The key that opens the microphone, from outside the window.

Wayland will not let an application listen for a key it does not have focus for, and
that is exactly the situation push-to-talk is for - you are in a game or a call, not
in Bonsai. Reading /dev/input directly would work but needs membership of the input
group, which is not something an assistant should be arranging.

So the compositor keeps the key and sends a word down a socket. Any compositor can do
it, the binding lives where the user's other bindings live, and Bonsai does not need
privileges it has no business having.
"""
import os
import socket

from PyQt6.QtCore import QThread, pyqtSignal

from .ptt import socket_path


class PushToTalk(QThread):
    """Listens for on/off/toggle on a socket in the user's runtime directory."""

    held = pyqtSignal(bool)
    toggled = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.path = socket_path()
        self.server = None
        self._stop = False

    def stop(self):
        self._stop = True
        if self.server is not None:
            # Poke it so the blocking read returns rather than waiting for a key.
            try:
                nudge = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                nudge.sendto(b"", self.path)
                nudge.close()
            except OSError:
                pass

    def run(self):
        try:
            # A stale socket file from a crash would make bind fail forever.
            if os.path.exists(self.path):
                os.unlink(self.path)
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.server.bind(self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            self.failed.emit(f"Could not listen for the push-to-talk key: {exc}")
            return
        try:
            while not self._stop:
                try:
                    data, _ = self.server.recvfrom(64)
                except OSError:
                    break
                word = data.decode(errors="replace").strip().lower()
                if word == "on":
                    self.held.emit(True)
                elif word == "off":
                    self.held.emit(False)
                elif word == "toggle":
                    self.toggled.emit()
        finally:
            if self.server is not None:
                self.server.close()
            try:
                os.unlink(self.path)
            except OSError:
                pass
