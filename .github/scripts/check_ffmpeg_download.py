#!/usr/bin/env python3
"""Prove this platform can fetch and unpack a working ffmpeg for itself.

Run on every runner, regardless of whether that runner already has ffmpeg.
The hosted images are inconsistent - Ubuntu ships one, Windows does not - so
without this the self-heal path would only ever be exercised by accident on
whichever images happen to lack it, which is exactly the path a student's
machine depends on.

Downloads into a temporary directory and throws it away. It does not touch
the install the rest of the workflow uses.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(os.environ.get("CLEAN_CUT_DIR")
               or ROOT / "Projects" / "clean-cut") / "scripts"
sys.path.insert(0, str(SCRIPTS))

import setup as S                                              # noqa: E402

key = (platform.system(), platform.machine())
print(f"platform: {key[0]} {key[1]}")
if key not in S.FFMPEG_BUILDS:
    sys.exit(f"FAIL no static build listed for {key}; "
             "a student here could not be self-healed")

url, kind = S.FFMPEG_BUILDS[key]
print(f"source:   {url}")
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    arc = tmp / f"ffmpeg.{kind}"
    S.download(url, arc)
    print(f"archive:  {arc.stat().st_size / 1048576:.0f} MB")
    got = S.extract_ffmpeg(arc, kind, tmp / "bin")
    print(f"binary:   {got.name}  {got.stat().st_size / 1048576:.0f} MB")

    missing = S.ffmpeg_lacks(str(got))
    if missing:
        sys.exit(f"FAIL the downloaded build is missing: {', '.join(missing)}")
    ver = subprocess.run([str(got), "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout.splitlines()
    print(f"version:  {ver[0] if ver else '?'}")
print("ok - this platform can heal its own ffmpeg")
