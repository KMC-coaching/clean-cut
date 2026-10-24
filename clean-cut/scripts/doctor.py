#!/usr/bin/env python3
"""Check this machine can actually run clean-cut, and say exactly what is missing.

    python3 doctor.py          # report
    python3 doctor.py --quiet  # exit code only

Written because "install ffmpeg" is not a dependency check. The pipeline needs
specific OPTIONAL build features - vidstab for stabilisation, libass for
captions, libx264 to encode - and an ffmpeg without them does not refuse the
job, it fails somewhere in the middle of a ten-minute render with an error
that names a filter and not the cause. Several distributions ship exactly such
a build.

Every line below is something that has actually broken, or that differs
between the machine this was built on and a machine it has never run on.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Prefer the ffmpeg clean-cut installed for itself, if there is one. A machine
# can carry a crippled system build - no vidstab, no libass, LGPL with no
# libx264 - and without this it would be used and the render would die in the
# middle. See scripts/cc_paths.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cc_paths as _ccp                                        # noqa: E402

_ccp.use_private_tools()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# filter name -> what stops working without it
FILTERS = {
    "vidstabdetect": "stabilising shaky footage (needs an ffmpeg built --enable-libvidstab)",
    "vidstabtransform": "stabilising shaky footage (same build flag)",
    "ass": "burning in captions (needs --enable-libass)",
    "zoompan": "the slow push on every b-roll insert",
    "unsharp": "sharpening after stabilisation",
    "loudnorm": "normalising to -14 LUFS",
    "dynaudnorm": "evening out speech level",
    "afftdn": "denoising",
    "acompressor": "compressing speech",
    "ebur128": "measuring loudness",
}
ENCODERS = {"libx264": "encoding the finished video (needs a GPL build)",
            "aac": "encoding the audio"}

OK, WARN, BAD = "ok", "warn", "BAD"
rows: list[tuple[str, str, str]] = []


def add(state: str, what: str, detail: str = "") -> None:
    rows.append((state, what, detail))


def total_ram_gb() -> float:
    """Physical RAM, or 0.0 if this platform will not say."""
    try:                                                        # Linux, macOS
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 1073741824
    except (ValueError, AttributeError, OSError):
        pass
    try:                                                        # Windows
        import ctypes                                           # noqa: PLC0415

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        return ms.ullTotalPhys / 1073741824
    except Exception:                                           # noqa: BLE001
        return 0.0


def ffmpeg_caps() -> tuple[set[str], set[str], str]:
    exe = shutil.which("ffmpeg")
    if not exe:
        return set(), set(), ""
    def run(args):
        try:
            return subprocess.run([exe, "-hide_banner", *args],
                                  capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return ""
    filters = {ln.split()[1] for ln in run(["-filters"]).splitlines()
               if len(ln.split()) > 1 and ln.startswith(" ")}
    encoders = {ln.split()[1] for ln in run(["-encoders"]).splitlines()
                if len(ln.split()) > 1 and ln.startswith(" ")}
    ver = run(["-version"]).splitlines()[0] if run(["-version"]) else ""
    return filters, encoders, ver


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--broll", default=str(Path.home() / ".clean-cut" / "broll-v2"))
    args = ap.parse_args()

    # ---- python and packages
    v = sys.version_info
    add(OK if v >= (3, 9) else BAD, f"python {v.major}.{v.minor}.{v.micro}",
        "" if v >= (3, 9) else "3.9 or newer required")
    for mod, why in (("cv2", "face detection and frame checks"),
                     ("numpy", "all measurement"),
                     ("PIL", "measuring caption width - without it widths are "
                             "guessed from character count, up to 80% wrong")):
        try:
            __import__(mod)
            add(OK, f"python package {mod}")
        except ImportError:
            add(BAD, f"python package {mod}", f"needed for {why}")
    have_asr = []
    for mod in ("mlx_whisper", "faster_whisper"):
        try:
            __import__(mod)
            have_asr.append(mod)
        except ImportError:
            pass
    # Name the one that can actually be installed HERE. On Ubuntu the old
    # message led with mlx-whisper, which does not install off Apple Silicon
    # at all, so the first instruction a Linux tester got was a dead end.
    apple = sys.platform == "darwin" and platform.machine() == "arm64"
    want = "mlx-whisper" if apple else "faster-whisper"
    add(OK if have_asr else BAD, "speech recognition",
        ", ".join(have_asr) if have_asr
        else f"run setup.sh, or: pip install {want}")

    # ---- ffmpeg and its build features
    filters, encoders, ver = ffmpeg_caps()
    if not ver:
        add(BAD, "ffmpeg", "not on PATH")
    else:
        add(OK, "ffmpeg", ver.split(" Copyright")[0])
        for f, why in FILTERS.items():
            add(OK if f in filters else BAD, f"  filter {f}",
                "" if f in filters else f"missing - no {why}")
        for e, why in ENCODERS.items():
            add(OK if e in encoders else BAD, f"  encoder {e}",
                "" if e in encoders else f"missing - no {why}")

    # ---- memory
    # A 6 GB Ubuntu VM got its ffmpeg OOM-killed mid-render at 3.8 GB RSS, and
    # nothing before that moment hinted it would. The SAME 51s video with the
    # same two inserts peaked at 1.7 GB on macOS and 5.6 GB on Ubuntu, so the
    # platform matters more than the video does and a macOS measurement is no
    # guide at all. Within a platform the shape is: an encode floor around
    # 0.9 GB at -preset slow, plus roughly 0.45 GB per b-roll insert held for
    # the whole run. Three filtergraph shapes were measured and all cost the
    # same, so that is the price of ffmpeg's overlay, not a wiring mistake.
    # 8 GB is the line because 6 GB demonstrably was not enough.
    ram = total_ram_gb()
    if ram:
        add(OK if ram >= 8 else WARN, "memory", f"{ram:.0f} GB"
            + ("" if ram >= 8 else
               "  - a 51s two-insert video peaked at 5.6 GB on Linux "
               "(1.7 GB on macOS). Close other apps, or pass --no-broll "
               "for a talking-head cut."))

    # ---- files that ship with the skill
    font = ROOT / "assets" / "Barlow-Bold.ttf"
    if not font.exists():
        add(BAD, "caption font", f"missing: {font}")
    else:
        try:
            from PIL import ImageFont                          # noqa: PLC0415
        except ImportError:
            # Don't blame the font for a missing Pillow. On a bare Ubuntu box
            # this read "present but will not load: No module named 'PIL'",
            # which sends a tester hunting for a font problem they do not have.
            add(WARN, "caption font", f"{font.name} present, not checked (needs PIL)")
        else:
            try:
                ImageFont.truetype(str(font), 80)
                add(OK, "caption font", font.name)
            except Exception as e:                              # noqa: BLE001
                add(BAD, "caption font", f"present but will not load: {e}")

    model = HERE / "yunet.onnx"
    sz = model.stat().st_size if model.exists() else 0
    add(OK if sz > 100_000 else BAD, "face model yunet.onnx",
        "" if sz > 100_000 else ("missing" if not sz else f"only {sz} bytes - re-run setup.sh"))

    # broll-manifest ships with the code and its absence IS a fault. The other
    # two are the client's own scripts, are supplied separately, and their
    # absence is a missing feature with a known fix - calling that "blocking"
    # made a correct, expected first install read as a broken one.
    p = ROOT / "reference" / "broll-manifest.json"
    add(OK if p.exists() else BAD, "reference/broll-manifest.json",
        "" if p.exists() else "missing - re-download clean-cut")

    absent = [r for r in ("master-scripts.json", "script-broll.json")
              if not (ROOT / "reference" / r).exists()]
    if absent:
        add(WARN, "script recognition",
            f"{len(absent)} file(s) not supplied: {', '.join(absent)}\n"
            f"       ask whoever sent you this for them, and save them in\n"
            f"       {ROOT / 'reference'}\n"
            "       Videos are still trimmed, captioned and levelled without\n"
            "       them; no b-roll is placed.")
    else:
        add(OK, "script recognition", "12 scripts and their b-roll decisions")

    # ---- the b-roll library
    man = ROOT / "reference" / "broll-manifest.json"
    if man.exists():
        clips = [c for c in json.loads(man.read_text())["clips"] if c["required"]]
        out = Path(args.broll).expanduser()
        missing = [c for c in clips
                   if not (out / c["file"]).exists()
                   or (out / c["file"]).stat().st_size != c["bytes"]]
        add(OK if not missing else WARN, "b-roll library",
            f"{len(clips) - len(missing)} of {len(clips)} required clips"
            + ("" if not missing else
               "  - run: python3 fetch_broll.py   (if that cannot reach Pexels, "
               "reference/broll-sources.md has every clip's download link and "
               "the filename to save it as)"))

    # ---- report
    bad = [r for r in rows if r[0] == BAD]
    warn = [r for r in rows if r[0] == WARN]
    if not args.quiet:
        print("clean-cut doctor\n" + "-" * 66)
        for state, what, detail in rows:
            mark = {OK: "  ok ", WARN: " warn", BAD: " BAD "}[state]
            print(f"{mark} {what}" + (f"   {detail}" if detail else ""))
        print("-" * 66)
        if bad:
            print(f"{len(bad)} blocking problem(s). This machine cannot run clean-cut yet:")
            for _, what, detail in bad:
                print(f"   {what.strip()} - {detail or 'missing'}")
        elif warn:
            print("Ready, with one thing to fetch:")
            for _, what, detail in warn:
                print(f"   {what} - {detail}")
        else:
            print("Ready. Everything this needs is present.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
