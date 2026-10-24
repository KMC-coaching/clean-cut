#!/usr/bin/env python3
"""Rebuild the curated b-roll library from its manifest.

    python3 fetch_broll.py                 # the 16 clips the bank needs (~403MB)
    python3 fetch_broll.py --all           # all 28 (~571MB)
    python3 fetch_broll.py --check         # verify what is there, download nothing

The library is 608MB of screened footage and does not belong in git, but it
cannot be re-harvested either: `build_broll_library.py` pulls FRESH clips and
names them by category order, so a second run puts different footage under the
same filenames while `script-broll.json` names exact files. Of 54 candidates
sourced in one pass, 54 were rejected - Euro notes, a hotel room, a child's
face, FRIGIDAIRE legible on an oven drawer. The screening is the value, so
this restores that exact set by its source IDs rather than rolling the dice
again.

Pexels refuses rapid requests with a 403, which looks exactly like "the clip
is gone" if you are not watching for it, so downloads are spaced and retried.

Each clip has two source URLs. `cdn` is Pexels' own file host, which answers a
request with no headers at all; `pexels_id` builds the www.pexels.com download
link, which sits behind a bot check. The CDN is tried first and the download
link is the fallback, since a re-encode on Pexels' side would change the CDN
filename while leaving the download link working. Both are listed per clip, for
a human to click, in `reference/broll-sources.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / "reference" / "broll-manifest.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDRS = {"User-Agent": UA, "Referer": "https://www.pexels.com/", "Accept": "*/*"}


def md5_2mb(p: Path) -> str:
    return hashlib.md5(p.read_bytes()[:2_000_000]).hexdigest()


def intact(p: Path, clip: dict) -> bool:
    return (p.exists() and p.stat().st_size == clip["bytes"]
            and md5_2mb(p) == clip["md5_2mb"])


def download(clip: dict, dest: Path, tries: int = 4) -> str:
    # The CDN first, the download link second. `cdn` is exactly where the
    # download link redirects to, but it is a plain file host with no bot
    # check in front of it, so it survives a network that pexels.com itself
    # refuses. The download link is kept as the fallback because a re-encode
    # on Pexels' side changes the CDN filename and leaves that link working.
    urls = [u for u in (clip.get("cdn"),
                        f"https://www.pexels.com/download/video/{clip['pexels_id']}/") if u]
    last = "no source url"
    for url in urls:
        for attempt in range(tries):
            try:
                req = urllib.request.Request(url, headers=HDRS)
                data = urllib.request.urlopen(req, timeout=180).read()
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code in (403, 429) and attempt < tries - 1:
                    wait = (2 ** attempt) * 6 + random.uniform(0, 4)
                    print(f"      {e.code}, waiting {wait:.0f}s")
                    time.sleep(wait)
                    continue
                break
            except Exception as e:                              # noqa: BLE001
                last = str(e)
                if attempt < tries - 1:
                    time.sleep(5)
                    continue
                break
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if not intact(dest, clip):
                got = dest.stat().st_size
                dest.unlink(missing_ok=True)
                last = (f"content did not match the manifest "
                        f"({got} bytes, expected {clip['bytes']})")
                break
            return ""
        if url is not urls[-1]:
            print(f"      {last} - trying the pexels.com link instead")
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / ".clean-cut" / "broll-v2"))
    ap.add_argument("--all", action="store_true", help="optional clips too")
    ap.add_argument("--check", action="store_true", help="verify only, download nothing")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"No manifest at {MANIFEST}")
        return 2
    man = json.loads(MANIFEST.read_text())
    clips = [c for c in man["clips"] if args.all or c["required"]]
    out = Path(args.out).expanduser()

    have, need = [], []
    for c in clips:
        (have if intact(out / c["file"], c) else need).append(c)

    print(f"library: {out}")
    print(f"  {len(have)} of {len(clips)} present and verified")
    if args.check:
        for c in need:
            p = out / c["file"]
            why = "missing" if not p.exists() else "size or checksum differs"
            print(f"    {c['file']:22s} {why}")
        return 1 if need else 0
    if not need:
        print("  nothing to fetch")
        return 0

    mb = sum(c["bytes"] for c in need) / 1048576
    print(f"  fetching {len(need)} clip(s), about {mb:.0f}MB. Pexels is rate-limited,")
    print("  so this is paced deliberately and takes a few minutes.")
    failed = []
    for i, c in enumerate(need, 1):
        print(f"  [{i}/{len(need)}] {c['file']} ({c['bytes']/1048576:.1f}MB)")
        err = download(c, out / c["file"])
        if err:
            print(f"      FAILED: {err}")
            failed.append((c["file"], err))
        time.sleep(random.uniform(1.5, 3.5))

    if failed:
        print(f"\n{len(failed)} clip(s) did not download:")
        for f, e in failed:
            print(f"  {f}: {e}")
        print("Re-run this; it skips what is already verified.")
        return 1
    print("\nlibrary complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
