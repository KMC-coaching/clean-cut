#!/usr/bin/env python3
r"""Prove a path with awkward characters survives the ffmpeg filtergraph.

    python3 test_paths.py

Exists because `_esc_filter_path` escaped a colon with one backslash, which
reads correctly and does not work. A Windows path always carries a drive
colon, so that was every Windows render failing, and nothing on a Mac was
going to notice. Each case below renders a real frame through the real
`ass=` filter rather than asserting on the escaped string, because the
question is never "does this look escaped" but "does ffmpeg accept it".

Windows is checked here as a path SHAPE, not as an operating system. That is
the honest limit of this file: it proves the escaping, not the platform.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autoedit import _esc_filter_path, CAPTION_FONT_NAME, ASSETS_DIR   # noqa: E402

ASS = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Alignment
Style: D,{CAPTION_FONT_NAME},30,&H00FFFFFF,2

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:01.00,D,PATH TEST
"""

# Every character that any of the three parsers treats as special, plus the
# shapes a real machine produces.
CASES = [
    "plain",
    "with space",
    "Dad's videos",              # apostrophe - common in a real home folder
    "a,comma",
    "a;semi",
    "a[bracket]",
    "a:colon",                   # the Windows drive, in a directory name
    "Videos (2026)",
    "all ,;[]:' at once",
]


# Windows forbids these outright in a path component, so a case using one
# cannot be created there, let alone rendered. Skipping is honest; pretending
# to have tested it would not be. The Windows DRIVE colon - the thing this
# file exists for - is checked separately below, as a string, because no
# machine but Windows can hold one.
ILLEGAL_ON_WINDOWS = set('<>:"|?*')


def impossible_here(name: str) -> str:
    if os.name == "nt" and (bad := ILLEGAL_ON_WINDOWS & set(name)):
        return f"windows cannot create a folder containing {''.join(sorted(bad))}"
    return ""


def run_case(root: Path, name: str) -> tuple[bool, str]:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    ass = d / "captions.ass"
    ass.write_text(ASS)
    out = d / "o.mp4"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-v", "error",
           "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=1",
           "-vf", f"ass={_esc_filter_path(ass)}:"
                  f"fontsdir={_esc_filter_path(ASSETS_DIR)}",
           "-frames:v", "3", "-c:v", "libx264", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
        return True, ""
    return False, (r.stderr.strip().splitlines() or ["no output"])[0][:80]


def main() -> int:
    print("filtergraph path escaping")
    print("-" * 66)
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name in CASES:
            why = impossible_here(name)
            if why:
                print(f"  skip {name}   {why}")
                continue
            ok, err = run_case(root, name)
            bad += not ok
            print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"   {err}" if err else ""))

    # The Windows shape, checked on the string because no Mac or Linux box can
    # hold a real drive letter. A bare "C:" here would mean the drive is being
    # handed to ffmpeg as an option separator.
    win = _esc_filter_path(Path(r"C:\Users\Tester\.clean-cut\out-work\captions.ass"))
    print("-" * 66)
    print(f"  windows path -> {win}")
    if "\\\\:" not in win or "\\" in win.replace("\\\\:", "").replace("\\\\\\'", ""):
        print("  FAIL windows drive colon is not escaped for the argument parser")
        bad += 1
    else:
        print("  ok   drive colon escaped, separators normalised to /")

    ran = sum(1 for c in CASES if not impossible_here(c)) + 1
    print("-" * 66)
    print(f"{ran - bad} of {ran} passed"
          + (f"  ({len(CASES) + 1 - ran} skipped on this platform)"
             if ran < len(CASES) + 1 else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
