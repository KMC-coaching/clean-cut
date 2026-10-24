#!/usr/bin/env python3
"""Where clean-cut keeps its things, and how to find the tools it installed.

Every other script asks this module rather than building the paths itself.
The paths differ by platform in exactly one way that matters - a virtualenv
puts its interpreter in `bin/` on macOS and Linux and in `Scripts/` on
Windows - and that one difference was enough to break every documented
command on Windows. It is answered here, once.

Stdlib only, on purpose: `doctor.py` and `setup.py` import this before
anything has been installed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Everything heavy lives outside the repo. A 1.4 GB virtualenv has no business
# inside a git checkout, and the b-roll library is shared across checkouts.
HOME_DIR = Path(os.environ.get("CLEAN_CUT_HOME", Path.home() / ".clean-cut"))
VENV_DIR = HOME_DIR / "venv"
BROLL_DIR = HOME_DIR / "broll-v2"

# Tools clean-cut installed for itself, because the machine did not have them
# and asking a student to install ffmpeg is how a setup stops being automatic.
BIN_DIR = HOME_DIR / "bin"

WINDOWS = os.name == "nt"
EXE = ".exe" if WINDOWS else ""


def venv_python() -> Path:
    """The interpreter inside our virtualenv, wherever this platform puts it."""
    return VENV_DIR / ("Scripts" if WINDOWS else "bin") / f"python{EXE}"


def private_ffmpeg() -> Path:
    """The ffmpeg we installed, whether or not it exists yet."""
    return BIN_DIR / f"ffmpeg{EXE}"


def use_private_tools() -> None:
    """Put our own bin directory first on PATH for this process.

    Called at import time by everything that shells out to ffmpeg. Without it
    a machine with a crippled system ffmpeg - no vidstab, no libass, an LGPL
    build with no libx264 - would keep using that one and fail in the middle
    of a render, while a perfectly good ffmpeg sat in ~/.clean-cut/bin.
    Ours is preferred when it exists; otherwise PATH is left untouched and
    the system's is used exactly as before.
    """
    if not BIN_DIR.is_dir():
        return
    cur = os.environ.get("PATH", "")
    first = cur.split(os.pathsep)[0] if cur else ""
    if first == str(BIN_DIR):
        return
    os.environ["PATH"] = str(BIN_DIR) + (os.pathsep + cur if cur else "")


def in_our_venv() -> bool:
    """True when the running interpreter is the one setup.py built.

    Compares sys.prefix, NOT the path of the executable. A virtualenv's
    `bin/python` is a SYMLINK to the base interpreter, so resolving both
    sides makes them identical and the test passes from any Python on the
    machine - which silently skipped the re-exec and ran the pipeline
    against the system site-packages, where the transcriber is not
    installed. sys.prefix is the environment, and it does not follow links.
    """
    if sys.prefix == sys.base_prefix:          # not in a virtualenv at all
        return False
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False
