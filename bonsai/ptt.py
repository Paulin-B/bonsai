#!/usr/bin/env python3
"""Tell a running Bonsai to open or close the microphone.

Deliberately standalone: it imports nothing from the package, because a push-to-talk
key runs this on every press and every release, and importing PyQt6 to send eleven
bytes would put a third of a second between pressing the key and being heard.

    python3 bonsai/ptt.py on | off | toggle

Wayland does not let an application grab a key it does not have focus for, which is
the whole problem with push-to-talk: you are talking while playing something else. So
the compositor holds the key and pokes this instead. On Hyprland 0.56, in Lua:

    hl.bind("SUPER + V", hl.dsp.exec_cmd("python3 <this> on"))
    hl.bind("SUPER + V", hl.dsp.exec_cmd("python3 <this> off"), { release = true })
"""
import os
import socket
import sys


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, "bonsai-ptt.sock")


def send(word):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        client.sendto(word.encode(), socket_path())
        return True
    except OSError:
        return False        # Bonsai is not running, or is not listening
    finally:
        client.close()


if __name__ == "__main__":
    word = (sys.argv[1] if len(sys.argv) > 1 else "toggle").lower()
    if word not in ("on", "off", "toggle"):
        sys.exit("say on, off or toggle")
    sys.exit(0 if send(word) else 1)
