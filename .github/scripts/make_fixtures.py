#!/usr/bin/env python3
"""Build throwaway scripts and b-roll so CI needs no real content.

    python3 make_fixtures.py <clean-cut-dir> <broll-dir>

The platform test must exercise script identification, b-roll placement and
the overlay chain. All three need a script bank and a clip library - and the
real ones are the client's twelve master scripts and 403 MB of licensed
Pexels footage, neither of which belongs in a repository that exists only to
run tests.

So CI writes its own. The script below is invented, the "footage" is ffmpeg
test patterns, and the anchors are placed deliberately: clear of the
protected hook, clear of any name reveal, and far enough apart to satisfy the
five-second minimum gap, so all three inserts actually get placed and the
zoompan/alphamerge/overlay chain is genuinely exercised on every platform.

None of this tests whether the real b-roll is any good. It tests whether this
machine can render it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Written to be read aloud by a speech synthesiser: short clauses, ordinary
# words, no numerals or symbols that each platform's voice says differently.
HOOK = ("Most people lose hours every single week to work a computer could "
        "have finished before breakfast.")

BODY = (
    "Here is the part that surprises them. "
    "The hours are not lost in one place. They leak away in small pieces. "
    "A file renamed by hand. A number copied from one window into another. "
    "Start with the dull repeated task, the one nobody wants on a Monday "
    "morning, and write it down step by step before you automate anything. "
    "Then measure how long the whole thing really takes, from the first "
    "click to the finished result, because the guess is always wrong. "
    "Only then should you build. Build the smallest version that works, "
    "run it beside the old way for a week, and keep careful notes. "
    "That is the whole method, and it works on almost any task."
)

# (anchor phrase, category, why). The anchors are plain sentences from the
# body above, chosen for position rather than meaning.
INSERTS = [
    ("the hours are not lost in one place", "ALPHA",
     "Fixture insert. Position chosen to clear the protected hook."),
    ("measure how long the whole thing really takes", "BRAVO",
     "Fixture insert. Position chosen to clear the previous insert by >5s."),
    ("run it beside the old way for a week", "CHARLIE",
     "Fixture insert. Position chosen to sit well before the end."),
]


def write_scripts(ref: Path) -> None:
    ref.mkdir(parents=True, exist_ok=True)
    scripts = [{
        "n": 1,
        "title": "Fixture Script",
        "seconds": 75,
        "hook": HOOK,
        "body": BODY,
    }]
    # A couple of decoys, so identification has to actually discriminate
    # rather than match the only thing on the shelf.
    for n, title, text in (
        (2, "Decoy Two", "Bread needs flour and water and time and salt. "
                         "Heat the oven while the dough is resting."),
        (3, "Decoy Three", "The tide goes out twice a day along this coast. "
                           "Boats wait on the mud until the water returns."),
    ):
        scripts.append({"n": n, "title": title, "seconds": 20,
                        "hook": text, "body": text})

    (ref / "master-scripts.json").write_text(json.dumps({
        "_source": "GENERATED FIXTURE - not real content. See "
                   ".github/scripts/make_fixtures.py",
        "_why": "CI must not carry anyone's copyrighted scripts.",
        "scripts": scripts,
    }, indent=1) + "\n", encoding="utf-8")

    (ref / "script-broll.json").write_text(json.dumps({
        "_what": "GENERATED FIXTURE - not real decisions.",
        "scripts": {
            "1": {
                "title": "Fixture Script",
                "inserts": [
                    {"anchor": a, "category": c, "lead": 0.0, "length": 3.5,
                     "why": w, "clip": f"{c}_1.mp4"}
                    for a, c, w in INSERTS
                ],
            }
        },
    }, indent=1) + "\n", encoding="utf-8")
    print(f"   scripts: {ref / 'master-scripts.json'}")


def write_broll(out: Path) -> None:
    """Vertical clips with motion in them, one per fixture category."""
    patterns = {
        "ALPHA":   "testsrc2=s=1080x1920:r=30:d=6",
        "BRAVO":   "smptebars=s=1080x1920:r=30:d=6",
        "CHARLIE": "rgbtestsrc=s=1080x1920:r=30:d=6",
    }
    for cat, src in patterns.items():
        dest = out / cat / f"{cat}_1.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                        "-f", "lavfi", "-i", src,
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                        "-pix_fmt", "yuv420p", str(dest)], check=True)
        print(f"   clip:    {dest}  {dest.stat().st_size // 1024} KB")


def main() -> int:
    # Split in two because of ordering: the scripts are pure JSON and must
    # exist BEFORE setup runs (doctor checks for them), while the clips need
    # ffmpeg, which on Windows does not exist until setup has installed it.
    # Doing both at once passed on Linux and macOS purely because those
    # runner images happen to ship ffmpeg, and failed on every Windows run.
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    want_scripts = "--broll-only" not in flags
    want_broll = "--scripts-only" not in flags

    cc = Path(args[0]).resolve()
    broll = Path(args[1]).expanduser().resolve() if len(args) > 1 else None
    print("writing CI fixtures (no real scripts, no licensed footage)")
    if want_scripts:
        write_scripts(cc / "reference")
    if want_broll:
        if broll is None:
            raise SystemExit("a b-roll directory is needed unless --scripts-only")
        write_broll(broll)
    # The manifest ships for real, and doctor checks the library against it.
    # Point it at what actually exists here.
    if want_scripts:
        (cc / "reference" / "broll-manifest.json").write_text(json.dumps({
            "_what": "GENERATED FIXTURE.",
            "required_count": 0,
            "clips": [],
        }, indent=1) + "\n", encoding="utf-8")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
