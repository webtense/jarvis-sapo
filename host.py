"""Which desktop JARVIS is running on.

The platform seam for the Linux port. macOS code paths stay exactly as the
upstream wrote them; each platform-specific module asks `is_linux()` at call
time and takes a Linux branch when it is true. It is a function, not a module
constant, so a test can pin it either way (see tests/conftest.py, which pins it
False by default so the upstream macOS suite keeps meaning what it meant).
"""
import os
import sys


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY"))
