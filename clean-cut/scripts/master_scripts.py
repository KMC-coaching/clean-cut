#!/usr/bin/env python3
"""Identify which of the twelve standard scripts a video is, from what is said.

Two things were being trusted that should not have been.

The **upload title**: video 04 is filed as "Curb Appeal" and is, word for word,
script 3, "Thinking About Buying?" - representation, negotiation, deposits, not
a lawn in sight. Constraining its b-roll by that title gave it paint and
flowerbeds.

The **keyword guess**: the old `detect_script` matched a handful of trigger
words against a list built by watching one student's delivered videos. It could
not tell script 6 ("buyers lose deposits and thousands spent on inspections")
from script 8, which is actually about inspections.

Now that the unpersonalised masters exist, neither is needed. The student reads
the master almost verbatim - only `[Name & Brokerage]` changes - so the
transcript can be matched against the real text and the answer is not a guess.

Matching is on 4-word shingles, scored by containment of the transcript in the
master. Containment rather than similarity because the student's take is a
*subset*: they fluff lines, skip a sentence, and add their own name and
brokerage, all of which shorten the overlap without making it the wrong script.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MASTERS = json.loads(
    (Path(__file__).resolve().parent.parent / "reference" / "master-scripts.json")
    .read_text())["scripts"]

# A confident identification. Measured across the twelve raw uploads: the right
# script scores far above this and the runner-up far below, so the gap does the
# work and the exact cutoff is not delicate.
MIN_SCORE = 0.35
MIN_MARGIN = 0.10


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _shingles(text: str, n: int = 4) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def _full(script: dict) -> str:
    return f"{script['hook']} {script['body']}"


def identify(transcript: str) -> tuple[dict | None, float, list[tuple[str, float]]]:
    """Return (script, score, ranked) - script is None when nothing is confident."""
    heard = _shingles(transcript)
    if not heard:
        return None, 0.0, []
    scored = []
    for s in MASTERS:
        master = _shingles(_full(s))
        # containment of what was HEARD inside the master
        hit = len(heard & master) / max(1, len(heard))
        scored.append((s, hit))
    scored.sort(key=lambda x: -x[1])
    ranked = [(s["title"], round(v, 3)) for s, v in scored[:3]]
    best, score = scored[0]
    runner = scored[1][1] if len(scored) > 1 else 0.0
    if score < MIN_SCORE or (score - runner) < MIN_MARGIN:
        return None, score, ranked
    return best, score, ranked


def hook_window(script: dict, words: list[dict]) -> list[float] | None:
    """Where the master's HOOK ends, on the cut timeline.

    The hook was a flat 0-3s guess. The masters mark it explicitly, and the
    real hooks run four to seven seconds - so a 3s guard was letting b-roll
    land on the back half of the line that is supposed to stop the scroll.

    Matched against the WORD STREAM, not the transcript lines. Video 05 ends
    its hook "...in the first week", and the line break fell between "first"
    and "week", so a per-line search found neither and silently fell back to
    the flat three seconds - leaving six seconds of hook unprotected on the
    one script that most needed b-roll.
    """
    tail = _words(script["hook"])[-3:]
    if not tail or not words:
        return None
    seq = [re.sub(r"[^a-z0-9']", "", w["w"].lower()) for w in words]
    i = _find_anchor(seq, tail, slack=3)
    if i is None:
        return None
    j = i
    k = 0
    while j < len(seq) and k < len(tail):
        if seq[j] == tail[k]:
            k += 1
        j += 1
    return [0.0, round(float(words[min(j, len(words)) - 1]["e"]) + 0.3, 2)]


if __name__ == "__main__":
    import sys
    txt = Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    s, sc, ranked = identify(txt)
    print(f"{(s or {}).get('title', 'UNIDENTIFIED')}   score={sc:.3f}")
    for t, v in ranked:
        print(f"   {v:.3f}  {t}")


# --------------------------------------------------------------------------
# Placing the per-script b-roll bank onto one student's timeline.
#
# The decision of WHAT goes WHERE was made once, by reading the master script
# and writing down a reason. All that happens here is finding the phrase that
# decision was pinned to, in this particular take, and checking it still fits.
#
# Every filter below DROPS an insert. None relocates one. A relocated insert is
# an insert nobody decided on - which is how the old floor manufactured filler.

BANK = json.loads(
    (Path(__file__).resolve().parent.parent / "reference" / "script-broll.json")
    .read_text())

MIN_GAP_S = 5.0
# The shortest insert in the delivered human edit. Nothing goes below it.
MIN_INSERT_S = 3.5
# Below this an insert is a flash, not a shot. An insert cut short by an
# ad-lib is dropped rather than shown for a moment.
MIN_USABLE_S = 2.2


# Words that carry no meaning on their own. Dropping them lets an anchor
# survive a paraphrase: "from day one to keys" has to reach "from day one to
# WHEN YOU GET THOSE keys", which is four extra words - more slack than is
# safe to allow on the whole phrase, but trivial on "day one keys".
_FILLER = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "with",
           "from", "by", "into", "and", "or", "that", "this", "your", "my",
           "our", "their", "is", "are", "was", "be", "it", "you", "we",
           "they", "so", "as", "when", "what", "who", "those", "these"}


def _content(ws: list[str]) -> list[str]:
    out = [w for w in ws if w not in _FILLER]
    return out if len(out) >= 2 else ws


def _find_anchor(seq: list[str], want: list[str], slack: int = 2) -> int | None:
    """Where the anchor phrase starts, tolerating a couple of extra words.

    Students read the master almost verbatim, not exactly. "People are buying
    their dream homes" does not contain the phrase "buying dream homes", and
    an exact match dropped the insert over the one line in script 10 that is
    unambiguously about a house. Matching in ORDER with up to `slack` words
    skipped keeps the anchor pinned to the same moment without pretending a
    paraphrase is a different script.

    Order is still required and the first word must match exactly, so this
    cannot drift onto an unrelated sentence the way a bag-of-words match
    would.
    """
    if not want:
        return None
    for i in range(len(seq)):
        if seq[i] != want[0]:
            continue
        j, k, skipped = i + 1, 1, 0
        while k < len(want) and j < len(seq) and skipped <= slack:
            if seq[j] == want[k]:
                k += 1
            else:
                skipped += 1
            j += 1
        if k == len(want):
            return i
    return None


def _anchor_span(seq: list[str], want: list[str]) -> tuple[int, int] | None:
    """(start, end-exclusive) of the anchor in the transcript.

    Tries the phrase as written first. If she paraphrased past what that
    tolerates, fall back to the content words with more room - "day one keys"
    still pins the same moment, and dropping the insert over a harmless
    rewording is how a video ends up with no b-roll at all.
    """
    for words, slack in ((want, 2), (_content(want), 5)):
        i = _find_anchor(seq, words, slack=slack)
        if i is None:
            continue
        # walk forward to find where the last wanted word actually landed
        k, j = 0, i
        while j < len(seq) and k < len(words):
            if seq[j] == words[k]:
                k += 1
            j += 1
        if k == len(words):
            return i, j
    return None


def _divergence_time(seq: list[str], words: list[dict], after: int,
                     want: list[str], script: dict) -> float | None:
    """When, after the anchor, the transcript stops following the master.

    Walks the master and the transcript forward together from the anchor and
    returns the time of the word where they part company. Two stray words are
    tolerated - Whisper mishears, and people say "a" for "the".
    """
    mw = _words(re.sub(r"\[[^\]]*\]", " ", f"{script['hook']} {script['body']}"))
    j = _find_anchor(mw, want, slack=0)
    if j is None:
        return None
    i, j = after, j + len(want)
    strikes = 0
    while i < len(seq) and j < len(mw):
        if seq[i] == mw[j]:
            i += 1
            j += 1
            continue
        if j + 1 < len(mw) and seq[i] == mw[j + 1]:        # she skipped a word
            i += 1
            j += 2
            continue
        strikes += 1
        if strikes > 2:
            return float(words[i]["s"])
        i += 1
    return None


def place_bank(script: dict, words: list[dict], total: float,
               do_not_cover: dict) -> tuple[list[dict], list[str]]:
    """Return (inserts, notes-on-what-was-dropped-and-why)."""
    spec = BANK["scripts"].get(str(script["n"]))
    if not spec or not spec.get("inserts"):
        gap = BANK.get("_gaps", {}).get(str(script["n"]))
        return [], ([f"no b-roll for this script by design: {gap}"] if gap else [])

    seq = [re.sub(r"[^a-z0-9']", "", w["w"].lower()) for w in words]
    out: list[dict] = []
    notes: list[str] = []

    blocked = [tuple(v) for k, v in do_not_cover.items() if v]

    for ins in spec["inserts"]:
        want = _words(ins["anchor"])
        span = _anchor_span(seq, want)
        hit = span[0] if span else None
        if hit is None:
            notes.append(f"{ins['category']}: she never said \"{ins['anchor']}\" - dropped")
            continue
        start = round(max(0.0, words[hit]["s"] + ins["lead"]), 2)
        end = round(min(total, start + ins["length"]), 2)

        # Stop the insert where she leaves the script. One student delivered
        # "Expert guidance through finding homes, FINDING OUT WHAT'S WRONG
        # WITH THE HOME BEFORE YOU MAKE AN OFFER, making competitive offers".
        # The anchor matched correctly, but the insert's fixed length carried
        # the dream-home shot over her ad-lib about what is wrong with a
        # house. The length was chosen against the master's version of that
        # line; it is only valid for as long as she is still reading it.
        div = _divergence_time(seq, words, span[1], want, script)
        if div is not None and div < end:
            end = round(max(start, div), 2)
            if end - start < MIN_USABLE_S:
                notes.append(f"{ins['category']} at {start}s: she goes off-script "
                             f"{div - start:.1f}s in, too soon for an insert - dropped")
                continue
            notes.append(f"{ins['category']} at {start}s cut to "
                         f"{end - start:.1f}s - she goes off-script after that")

        clash = next((b for b in blocked if start < b[1] and end > b[0]), None)
        if clash:
            # Format the numbers, don't print the tuple: these are numpy
            # scalars, so repr() put "np.float64(12.98)" in front of a person.
            notes.append(f"{ins['category']} at {start:.1f}s overlaps a "
                         f"protected stretch ({float(clash[0]):.1f}-"
                         f"{float(clash[1]):.1f}s) - dropped")
            continue
        if end - start < 1.5:
            notes.append(f"{ins['category']} at {start}s had only "
                         f"{end - start:.1f}s before the end - dropped")
            continue
        if out and start - out[-1]["end"] < MIN_GAP_S:
            # Two inserts beat one long one. Before dropping this, try taking
            # the time out of the PREVIOUS insert - shortening is not
            # relocating, the decision of what goes where still stands, and it
            # never goes below the floor a human actually cut.
            room = round(start - MIN_GAP_S, 2)
            if room - out[-1]["start"] >= MIN_INSERT_S:
                was = out[-1]["end"] - out[-1]["start"]
                out[-1]["end"] = room
                notes.append(f"shortened the {out[-1]['category']} insert from "
                             f"{was:.1f}s to {room - out[-1]['start']:.1f}s so the "
                             f"{ins['category']} insert could stay")
            else:
                notes.append(f"{ins['category']} at {start}s is "
                             f"{start - out[-1]['end']:.1f}s after the previous insert "
                             f"(needs {MIN_GAP_S}, and shortening it would go under "
                             f"{MIN_INSERT_S}s) - dropped")
                continue
        if out and out[-1]["category"] == ins["category"]:
            notes.append(f"two {ins['category']} inserts in a row - dropped the second")
            continue
        if any(o["clip"] == ins["clip"] for o in out):
            notes.append(f"{ins['clip']} already used in this video - dropped")
            continue
        out.append({"start": start, "end": end, "category": ins["category"],
                    "clip": ins["clip"], "why": ins["why"]})
    return out, notes


# --------------------------------------------------------------------------
def correct_against_master(words: list[dict], script: dict) -> list[tuple[str, str]]:
    """Fix Whisper's mishearings in place, using the script she was reading.

    04's captions shipped "The right agent knows the strategies that WHEN" and
    "Don't BEAT another buyer who loses out". She said "win" and "be". Burning
    a misheard word into a caption is one of the most visible things that can
    go wrong, and it is the kind of error no audit catches - the text is
    perfectly well-formed, it is just not what she said.

    Only a SINGLE word is ever replaced, and only when the words either side
    of it already agree with the master. A student reading from a script does
    not substitute one word mid-phrase; Whisper does it constantly. Insertions
    and deletions are left alone, because that is where the real differences
    live - their name, their brokerage, a line they skipped, an ad-lib.

    Returns the list of (heard, corrected) pairs so the change is visible.
    """
    import difflib

    # "three" and "3" are the same word said aloud; the master just happens to
    # write it as a numeral. Rewriting a correctly-heard word to match the
    # script's typography is not an error fix, it is meddling.
    _NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
            "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
            "ten": "10", "twelve": "12", "twenty": "20"}

    def _same_number(a: str, b: str) -> bool:
        return _NUM.get(a, a) == _NUM.get(b, b)

    master = re.sub(r"\[[^\]]*\]", " ", f"{script['hook']} {script['body']}")
    mw = _words(master)
    hw = [_words(w["w"])[0] if _words(w["w"]) else "" for w in words]

    fixed: list[tuple[str, str]] = []
    sm = difflib.SequenceMatcher(a=hw, b=mw, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace" or (i2 - i1) != 1 or (j2 - j1) != 1:
            continue
        heard, want = hw[i1], mw[j1]
        if not heard or not want or heard == want or _same_number(heard, want):
            continue
        # Both neighbours must already agree, so we know we are inside a run
        # of the script rather than somewhere she went off-script.
        if i1 == 0 or i2 >= len(hw) or hw[i1 - 1] != mw[j1 - 1] or hw[i2] != mw[j2]:
            continue
        # Keep the original capitalisation and any trailing punctuation.
        old = words[i1]["w"]
        tail = re.sub(r"^[\w']+", "", old)
        new = want.capitalize() if old[:1].isupper() else want
        words[i1]["w"] = new + tail
        fixed.append((old, words[i1]["w"]))
    return fixed
