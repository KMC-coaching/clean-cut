#!/usr/bin/env python3
"""Build a talking-head-shaped test video on whatever runner this is.

    python3 make_test_video.py OUT.mp4 [seconds]

No sample video is committed, for two reasons: the real student uploads are
recordings of identifiable people and have no business in a repository, and a
file checked in once would stop representing anything. This speaks one of the
twelve master scripts through the operating system's own text-to-speech and
puts it over moving video, so every runner gets a clip with real speech in it
that nobody has to be in.

The picture is `testsrc2` rather than a flat colour on purpose: a still frame
would trip the audit's own "no still run longer than 0.4s" rule and report a
failure that is an artefact of the test rather than of the code.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import os

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


_CC = _find_clean_cut(ROOT)
SCRIPTS = Path(os.environ.get("CLEAN_CUT_SCRIPTS")
               or _CC / "reference" / "master-scripts.json")

# Use the ffmpeg clean-cut installed for itself. On Windows the runner has
# none of its own, so calling plain `ffmpeg` here died with WinError 2 even
# though setup had just put a working one in ~/.clean-cut/bin. Resolving it
# the same way the editor does means this helper does not depend on the
# workflow remembering to extend PATH.
sys.path.insert(0, str(_CC / "scripts"))
import cc_paths as _ccp                                        # noqa: E402

_ccp.use_private_tools()
WANT_SCRIPT = 4


def script_text(limit_words: int) -> tuple[str, str]:
    """The opening of a real master script, so identification is exercised.

    Hook then body, in that order, because that is the order a student records
    them in and the order the identifier matches against.
    """
    scripts = json.loads(SCRIPTS.read_text(encoding="utf-8"))["scripts"]
    # #4 on purpose. Its three b-roll anchors all sit clear of the hook and it
    # has no name reveal, so all three inserts actually get placed - which
    # means every runner exercises the zoompan/alphamerge/overlay chain and
    # the three-insert memory peak, not just a talking head. Script #1 drops
    # both of its inserts for perfectly correct reasons and would have tested
    # none of that.
    s = next((x for x in scripts if x["n"] == WANT_SCRIPT), min(scripts, key=lambda d: d["n"]))
    full = f"{s['hook']} {s['body']}".strip()

    # Stop on a sentence, never mid-phrase. Cutting at a word count left the
    # clip ending on "...when it's time to", and the audit correctly failed
    # the dangling caption - a fault in the test material being reported as a
    # fault in the code, which is the worst kind of false alarm to ship.
    if len(full.split()) > limit_words:
        kept, n = [], 0
        for sentence in re.split(r"(?<=[.?!])\s+", full):
            if n and n + len(sentence.split()) > limit_words:
                break
            kept.append(sentence)
            n += len(sentence.split())
        full = " ".join(kept)
    return full, f"#{s['n']} {s['title']}"


def speak(text: str, wav: Path) -> None:
    system = platform.system()
    if system == "Darwin":
        aiff = wav.with_suffix(".aiff")
        subprocess.run(["say", "-o", str(aiff), text], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                        "-i", str(aiff), "-ar", "48000", "-ac", "1", str(wav)],
                       check=True)
    elif system == "Windows":
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SetOutputToWaveFile('{wav}'); "
            f"$s.Speak(@'\n{text}\n'@); "
            "$s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    else:
        subprocess.run(["espeak-ng", "-s", "145", "-w", str(wav), text], check=True)
    if not wav.exists() or wav.stat().st_size < 10_000:
        raise SystemExit(f"text-to-speech produced nothing usable at {wav}")


def main() -> int:
    out = Path(sys.argv[1]).resolve()
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 70
    # Roughly 150 words a minute, and a little over so -shortest trims to audio.
    text, which = script_text(int(seconds * 2.6))
    print(f"speaking {len(text.split())} words of {which} on {platform.system()}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "speech.wav"
        speak(text, wav)
        print(f"   audio: {wav.stat().st_size // 1024} KB")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30",
                        "-i", str(wav), "-shortest",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
                       check=True)
    print(f"   video: {out}  {out.stat().st_size // 1048576} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
