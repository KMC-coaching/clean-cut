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
import urllib.error
import urllib.request
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[2]

def _find_clean_cut(root: Path) -> Path:
    """Locate the skill whether this is the work repo or the test repo.

    The work repo keeps it at Projects/clean-cut; the platform-test repo keeps
    it at clean-cut/. Hard-coding either one means the helper only runs in one
    of the two places, which is how the first Windows run died before it got
    anywhere near ffmpeg.
    """
    env = os.environ.get("CLEAN_CUT_DIR")
    if env:
        return Path(env).resolve()
    for rel in ("clean-cut", "Projects/clean-cut"):
        if (root / rel / "scripts" / "setup.py").is_file():
            return root / rel
    raise SystemExit(f"cannot find the clean-cut folder under {root}")


sys.path.insert(0, str(_find_clean_cut(ROOT) / "scripts"))

import setup as S                                              # noqa: E402

key = (platform.system(), platform.machine())
print(f"platform: {key[0]} {key[1]}")
if key not in S.FFMPEG_BUILDS:
    sys.exit(f"FAIL no static build listed for {key}; "
             "a student here could not be self-healed")

urls, kind = S.FFMPEG_BUILDS[key]

# Report EVERY listed source, not just the one that happens to work. A dead
# primary that a fallback quietly rescues is exactly the state that rotted
# unnoticed last time, so it has to be visible even on a passing run.
dead = []
for u in urls:
    req = urllib.request.Request(u, method="HEAD", headers={
        "User-Agent": "Mozilla/5.0 (clean-cut setup)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"source:   {r.status} {u}")
    except urllib.error.HTTPError as e:
        # Only "the file is not there any more" counts as dead. A 503 is the
        # publisher rate-limiting a burst of runners, and calling that a dead
        # url would turn a busy afternoon into a red build.
        if e.code in (404, 410):
            print(f"source:   GONE {u}  ({e.code})")
            dead.append(u)
        else:
            print(f"source:   {e.code} {u}  (transient, not treated as gone)")
    except Exception as e:                                     # noqa: BLE001
        print(f"source:   unreachable {u}  ({e}) - transient, not treated as gone")
if dead and len(dead) == len(urls):
    sys.exit("FAIL every listed source for this platform is gone")
if dead:
    print(f"WARNING {len(dead)} of {len(urls)} sources are gone; "
          "the fallback is carrying this platform - replace the gone one")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    arc = tmp / f"ffmpeg.{kind}"
    live = [u for u in urls if u not in dead]
    S.download(live[0], arc)
    print(f"archive:  {arc.stat().st_size / 1048576:.0f} MB from {live[0]}")
    got = S.extract_ffmpeg(arc, kind, tmp / "bin")
    print(f"binary:   {got.name}  {got.stat().st_size / 1048576:.0f} MB")

    missing = S.ffmpeg_lacks(str(got))
    if missing:
        sys.exit(f"FAIL the downloaded build is missing: {', '.join(missing)}")
    ver = subprocess.run([str(got), "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout.splitlines()
    print(f"version:  {ver[0] if ver else '?'}")
print("ok - this platform can heal its own ffmpeg")
