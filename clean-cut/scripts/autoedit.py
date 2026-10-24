#!/usr/bin/env python3
"""
autoedit.py - turn one raw talking-head video into a finished vertical cut.

    python3 autoedit.py INPUT.mp4 --out FINISHED.mp4

What it does, in order. Every step is measured, not guessed:

  1. Transcribes the video with word-level timestamps (local, no API, no cost).
  2. Finds retakes - a line said twice because the first attempt was fumbled -
     and keeps the better take. This is the one piece of real editorial
     judgement in the pipeline; see find_retakes() for how it decides.
  3. Trims the silent head and tail.
  4. Punches in, and punches in tighter on the closing line.
  5. Picks b-roll slots from what is being said, never over the opening hook
     or the speaker's own name, and fills them from a pre-screened library.
  6. Burns word-level captions, drawn AFTER b-roll so they sit on top of it.
  7. Denoises, compresses and normalizes to -14 LUFS, which is what social
     platforms target. (Same target as the headliner-cut skill.)

Writes a JSON report next to the output so every decision can be audited.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
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

W, H = 1080, 1920
CARD_W, CARD_H, CARD_R = 940, 1660, 56
TARGET_LUFS = -14.0

# b-roll never covers these: the hook is what stops the scroll, and the
# speaker's name reveal is the one moment the viewer must see a face.
HOOK_PROTECT_S = 4.0
INTRO_RE = re.compile(r"\b(hi|hey|i'm|im|my name)\b", re.I)

# Concrete-noun triggers -> the category of clip that illustrates them.
# Keep these literal: a vague trigger pulls a vague clip.
#
# Category names must match the folder names in the library and the keys in
# build_broll_library.CATEGORIES. They were derived from the b-roll the human
# editors actually used across the 12-script set - see
# reference/script-broll-map.md.
#
# Order matters: the first match wins, so the most specific patterns go first.
#
# Each entry is (pattern, category, weight). Weight is how strong a cue the
# word is: a specific concrete noun like "carpet" or "paint" almost always
# means the speaker is describing that thing, while "home" appears in every
# other sentence of every script and usually means nothing in particular.
# Scoring by weight is what stopped "home" in "don't buy a car during escrow,
# dream home..." from pulling a house exterior.
TRIGGERS = [
    # --- strong, specific: the speaker is naming a thing on screen ---
    (r"\bcarpet\b|\bflooring\b|\bhardwood\b|\bwear and tear\b|\bstains?\b", "flooring", 3),
    (r"\bpaint(ing|ed)?\b|\bpower.?wash\b|\bpressure.?wash\b|\brepaint", "curb_prep", 3),
    (r"\blawn\b|\blandscap\w*|\bmow\b|\bmulch\b|\bflower ?beds?\b|\bshrubs?\b|\bweeds?\b|\byard\b|\bgarden\b",
     "lawn", 3),
    (r"\binspect\w*|\broof\b|\bfurnace\b|\bhvac\b|\bplumbing\b|\belectrical\b|\bfoundation\b",
     "inspection", 3),
    (r"\bkeys?\b", "keys", 3),
    (r"\bdown payment\b|\bout of pocket\b|\bclosing costs?\b|\$[\d,.]+ ?k?\b", "money_cash", 3),
    (r"\bstag(e|ed|ing)\b|\bdeclutter\w*|\bcountertops?\b|\bkitchen\b|\bliving room\b",
     "interior_staged", 3),
    (r"\bsocial media\b|\bdrone\b|\bvideo tour\b|\blisting photos?\b|\bprofessional photos?\b",
     "marketing_video", 3),
    (r"\bpre.?qualif\w*|\bpre.?approv\w*|\bunderwrit\w*|\bpaperwork\b|\bcontract\b",
     "documents", 3),
    (r"\bequity\b|\bappreciat\w*|\bbuild wealth\b|\binvestment\b", "money_growth", 3),

    # --- medium ---
    (r"\bcurb appeal\b|\bfirst impression\b|\bfront door\b", "curb_prep", 2),
    (r"\bloans?\b|\bmortgage\b|\bgrants?\b|\bapplication\b|\blender\b", "documents", 2),
    (r"\bmoney\b|\bcash\b|\bdollars?\b|\bsavings?\b", "money_cash", 2),
    (r"\bmarketing\b|\bcamera\b|\bfilm\w*", "marketing_video", 2),
    (r"\brepairs?\b|\bissues?\b|\bmaintenance\b", "inspection", 2),

    # --- weak: common words that only count when nothing better is nearby ---
    (r"\bneighborhood\b|\bcurb\b|\bexterior\b|\bdriveway\b", "curb_exterior", 1),
    (r"\bhomes?\b|\bhouse\b|\bproperty\b", "curb_exterior", 1),
    (r"\bclosing\b|\bmove in\b|\bmoving\b", "keys", 1),
    (r"\bprice\b|\bcost\b|\bvalue\b|\bworth\b", "money_growth", 1),
]

# Words that make a nearby trigger meaningless: the speaker is telling you NOT
# to do the thing, or it is hypothetical. "Don't spend your closing money on a
# car" should not pull a picture of keys.
NEGATION = re.compile(r"\b(don'?t|do not|never|avoid|without|instead of|rather than|no need)\b")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def video_rotation(path: str) -> int:
    """Display-matrix rotation in degrees, 0 if none.

    Phones store portrait video as landscape plus a rotation flag. ffmpeg
    normally applies it for us - but vidstabtransform passes the flag through
    to the OUTPUT as well, so the player rotates an already-upright frame and
    the delivered video plays sideways. Only stabilised clips were affected,
    which is why it survived three rounds of testing unnoticed.
    """
    err = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path],
                         capture_output=True, text=True).stderr
    m = re.search(r"rotation of (-?[\d.]+)", err)
    return int(float(m.group(1))) if m else 0


def normalise_rotation(src: str, work: Path) -> str:
    """Bake a phone's rotation flag into the pixels, once, up front.

    Phones store portrait video as landscape plus a display-matrix flag.
    ffmpeg consumes that flag in a SIMPLE -vf chain, but in the filter_complex
    graph this pipeline uses it survives as far as the overlay that composites
    b-roll, and the muxer then writes it onto the output. The player rotates an
    already-upright frame and the finished video plays on its side.

    Every attempt to fix it inside the graph failed or made it worse:
    -noautorotate plus a manual transpose wrote the flag itself,
    -map_metadata -1 does not touch side data, and a -c copy remux cannot
    strip it. Normalising at the front door is the one thing that works, and
    it also means everything downstream - shake measurement, stabilisation,
    caption geometry - sees the same upright frame.

    Confirmed by bisection: with --no-broll the output is clean, with b-roll it
    is not, and the same graph over a normalised source is clean either way.
    """
    rot = video_rotation(src)
    if not rot:
        return src
    out = str(work / "normalised.mp4")
    print(f"      source carries a {rot} deg rotation flag - baking it in")
    # Long side capped at 2560: ample for a 1080x1920 output even after the
    # tightest 0.86 punch, and it keeps the stabiliser off full 4K.
    run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", src,
         "-vf", "scale='min(2560,iw)':'min(2560,ih)':force_original_aspect_ratio=decrease,format=yuv420p",
         "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
         "-c:a", "copy", "-y", out])
    return out


def probe(path: str) -> dict:
    """Duration, size and whether there is an audio track at all."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        out = None                   # some ffmpeg builds ship without ffprobe
    if out is None or out.returncode != 0:
        err = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path],
                             capture_output=True, text=True).stderr
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
        dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
        wh = re.search(r"(\d{2,5})x(\d{2,5})", err)
        return {"duration": dur,
                "width": int(wh.group(1)) if wh else 0,
                "height": int(wh.group(2)) if wh else 0,
                "has_audio": "Audio:" in err}
    d = json.loads(out.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    return {"duration": float(d["format"]["duration"]),
            "width": int(v.get("width", 0)),
            "height": int(v.get("height", 0)),
            "has_audio": any(s["codec_type"] == "audio" for s in d["streams"])}


# --------------------------------------------------------------------------
# 1. transcription
# --------------------------------------------------------------------------

def _wav_samples(wav: Path):          # -> np.ndarray, imported inside
    """The 16 kHz mono wav ffmpeg just wrote, as float32 in [-1, 1].

    Whisper's own expected input format. stdlib `wave` plus numpy, so there is
    no third decoder to drift out from under us.
    """
    import wave                                               # noqa: PLC0415

    import numpy as np                                        # noqa: PLC0415
    with wave.open(str(wav), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise RuntimeError(
                f"expected 16-bit mono, got {w.getsampwidth() * 8}-bit "
                f"{w.getnchannels()}ch - the ffmpeg decode above changed")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def transcribe(video: str, workdir: Path) -> dict:
    """Word-level transcript. Prefers MLX on Apple Silicon, falls back to
    faster-whisper elsewhere. Both run locally - nothing is uploaded."""
    wav = workdir / "audio.wav"
    run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", video,
         "-vn", "-ac", "1", "-ar", "16000", "-y", str(wav)])

    try:
        import mlx_whisper                                    # noqa: PLC0415
        r = mlx_whisper.transcribe(
            str(wav), path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            word_timestamps=True, language="en")
        return r
    except ImportError:
        pass

    try:
        from faster_whisper import WhisperModel               # noqa: PLC0415
    except ImportError:
        sys.exit("No local transcriber found. Install mlx-whisper (Apple "
                 "Silicon) or faster-whisper (everything else).")

    model = WhisperModel("large-v3", device="auto", compute_type="auto")
    # Hand over SAMPLES, not a path. Given a path, faster-whisper decodes it
    # with PyAV - and faster-whisper 1.2.1 still calls av.open(...,
    # metadata_errors=...), which PyAV 19 removed, while its own requirement
    # is an unbounded `av>=11`. So a fresh install today resolves to a
    # combination that raises TypeError on the first transcription. ffmpeg has
    # already decoded this file to 16 kHz mono four lines above; passing the
    # samples skips PyAV altogether and takes that whole dependency out of the
    # path rather than pinning around it.
    #
    # Only macOS-on-Apple-Silicon reaches the MLX branch, so until this ran on
    # Ubuntu nothing had ever executed the line below.
    segs, _ = model.transcribe(_wav_samples(wav), word_timestamps=True,
                               language="en")
    out = []
    for s in segs:
        out.append({"start": s.start, "end": s.end, "text": s.text,
                    "words": [{"word": w.word, "start": w.start, "end": w.end}
                              for w in (s.words or [])]})
    return {"segments": out, "text": " ".join(s["text"] for s in out)}


# --------------------------------------------------------------------------
# 2. retakes - the only real editorial judgement here
# --------------------------------------------------------------------------

def _norm(t: str) -> list[str]:
    return re.findall(r"[a-z0-9$%']+", t.lower())


def containment(a: list[str], b: list[str]) -> float:
    """Fraction of the SHORTER word list that appears inside the longer one.

    Plain similarity is the wrong measure for a retake. When someone fumbles a
    line and says it again more fully, the two takes differ a lot in length, so
    overall similarity reads low even though one is clearly contained in the
    other. Containment catches exactly that shape.
    """
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    m = difflib.SequenceMatcher(None, short, long)
    return sum(bl.size for bl in m.get_matching_blocks()) / max(1, len(short))


def speech_level(workdir: Path):
    """RMS of the clip's audio, and what speech measures in it.

    Returns (rms_fn, reference) or (None, 0). The reference is the median
    level over the transcribed speech, so a quiet recording is judged against
    itself rather than an absolute threshold.
    """
    wav = workdir / "audio.wav"
    if not wav.exists():
        return None, 0.0
    try:
        import wave                                            # noqa: PLC0415
        import numpy as np                                     # noqa: PLC0415
        with wave.open(str(wav), "rb") as wf:
            sr = wf.getframerate()
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    except Exception:
        return None, 0.0
    if not len(pcm):
        return None, 0.0

    def rms(a: float, b: float) -> float:
        seg = pcm[int(a * sr):int(b * sr)]
        return float(np.sqrt(np.mean(seg.astype(float) ** 2))) if len(seg) else 0.0

    return rms, 0.0


def verify_tail(workdir: Path, tr: dict, master_text: str | None) -> list[dict]:
    """Drop tail segments a second transcription pass does not agree with.

    Whisper's inventions are not reproducible. The same video gave "It's like
    a mess" on one run, nothing on two others, and "you / the right agent / I
    like the right agent" on a fourth. Real speech comes back the same every
    time; that is the whole difference, and it is the only signal that has
    held across every case here.

    Text rules cannot do it - "It's like a mess" and "Powered by Keller
    Williams" are both short, well formed and absent from the script, and one
    is junk while the other is a sign-off a student really said. Silence
    cannot do it either: on a phone recording the room tone after the last
    word measured 383 against 762 for speech, which is nowhere near quiet.

    So the tail gets transcribed again on its own and anything the second pass
    does not also hear is dropped.
    """
    segs = tr.get("segments") or []
    if len(segs) < 4 or not master_text:
        return []
    mw = set(re.findall(r"[a-z0-9']+", master_text.lower()))

    def in_master(txt: str) -> float:
        w = re.findall(r"[a-z0-9']+", txt.lower())
        return sum(1 for x in w if x in mw) / max(1, len(w))

    # where the script itself stops: the last segment that is mostly script
    close = None
    for i in range(len(segs) - 1, -1, -1):
        if in_master(segs[i]["text"]) >= 0.7 and len(segs[i]["text"].split()) >= 4:
            close = i
            break
    if close is None or close >= len(segs) - 1:
        return []
    tail = segs[close + 1:]
    start = float(segs[close]["end"])

    wav = workdir / "audio.wav"
    if not wav.exists():
        return []
    clip = workdir / "tailverify.wav"
    try:
        run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{max(0.0, start - 0.5):.2f}", "-i", str(wav),
             "-ac", "1", "-ar", "16000", "-y", str(clip)])
        import mlx_whisper                                     # noqa: PLC0415
        second = mlx_whisper.transcribe(
            str(clip), path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            language="en")
    except Exception:
        return []
    finally:
        clip.unlink(missing_ok=True)

    heard = set(re.findall(r"[a-z0-9']+",
                           " ".join(s["text"] for s in second.get("segments", [])).lower()))
    drop = []
    for s in tail:
        w = re.findall(r"[a-z0-9']+", s["text"].lower())
        if not w:
            drop.append(s)
            continue
        agree = sum(1 for x in w if x in heard) / len(w)
        if agree < 0.6:
            drop.append(s)
    return drop


def strip_hallucination(segments: list[dict],
                        master_text: str | None = None,
                        workdir: Path | None = None,
                        ) -> tuple[list[dict], list[dict]]:
    """Drop Whisper's looping output at the tail of a file.

    Whisper repeats itself when it runs past real speech into room tone. One
    video ended with "We're here to help you." six times followed by "Thank
    you." - none of it spoken. It is not just wrong captions: the invented
    words carry timestamps running to the end of the file, so the tail-trim
    finds no dead air and the cut collapses to nothing. That video lost 2.07s
    where it should have lost far more.

    A loop is a run of near-identical short segments at the END. We only look
    at the tail, because genuine repetition earlier ("don't wait, don't wait")
    is a rhetorical device and should be kept.
    """
    if len(segments) < 5:
        return segments, []

    # The master script is the authority on what counts as script content.
    # A trailing segment that is in the script is not chatter however it is
    # punctuated, and nothing below gets to drop it.
    def _in_script(txt: str) -> bool:
        if not master_text:
            return False
        words = re.findall(r"[a-z0-9']+", txt.lower())
        if not words:
            return False
        mw = re.findall(r"[a-z0-9']+", master_text.lower())
        # A short tail can still be the end of the script. Video 10 finishes
        # "...programs exist to HELP YOU." - two words, recovered from audio
        # Whisper had given up on, and then thrown away again because two
        # words is under the shingle minimum. Look for them verbatim instead.
        if len(words) < 3:
            n = len(words)
            return any(mw[i:i + n] == words for i in range(len(mw) - n + 1))
        shing = {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
        master = {tuple(mw[i:i + 3]) for i in range(len(mw) - 2)}
        return len(shing & master) / max(1, len(shing)) >= 0.45

    # Trailing garbage that is not a loop. Whisper also degrades into noise at
    # the end of a file without repeating itself - one upload ended with
    # "gel membership Team\ufffd". A replacement character, stray non-Latin
    # script, or a fragment with no verb after a complete closing sentence is
    # transcription debris, and burning it into captions looks broken.
    def _garbage(txt: str) -> bool:
        s = txt.strip()
        if not s:
            return True
        if "\ufffd" in s:
            return True
        latin = sum(1 for c in s if c.isascii())
        return latin / max(1, len(s)) < 0.85

    def _impossible(seg: dict) -> bool:
        """Words crammed into a span nobody could have said them in.

        04 ended with "And you can't have a name." and "The right agent knows
        the idea of the business." - six and nine words, occupying 0.1s each.
        Around sixty words a second. Neither word count nor punctuation can
        tell that from a real sign-off, because as text it is perfectly
        ordinary; what gives it away is physics.

        Normal delivery is two to four words a second. Eight is faster than
        anyone in these videos has ever spoken, so nothing real is at risk.
        """
        dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
        n = len(seg.get("text", "").split())
        if n < 2:
            return False
        if dur <= 0.05:
            return True
        return n / dur > 8.0

    def _chatter(txt: str) -> bool:
        """A fragment after the script has finished.

        One upload ended: "...guide you to home ownership success." then
        "He's going to talk to you to..." then "No." That is the student
        talking after the take, and it is a third of a sentence - it reads as
        a mistake the moment it is captioned. A tail segment is chatter when
        it is very short, or trails off unfinished.
        """
        s = txt.strip()
        if not s:
            return True
        words = len(s.split())
        # "Does not end in a full stop" was the test here, and it was wrong in
        # a way that destroyed content. Whisper splits mid-sentence all the
        # time, so a segment ending "...state" is an ordinary split, not
        # chatter. On video 10 the final segment ended "dozens of op", got
        # popped, and the loop then cascaded backwards through the rest of the
        # script: 54.5s of a 40s script collapsed to 23.2s and the transcript
        # stopped mid-sentence.
        #
        # Real chatter is SHORT. A trailing fragment that is also a whole
        # clause is almost always the student still reading.
        if s.endswith(("...", "-", "—")) and words < 8:
            return True
        return words < 4

    # A ceiling on the tail trim, independent of whether any single rule is
    # right. The trim is a tidy-up; if it wants a quarter of the video, the
    # rule is broken, not the video. Video 10 lost 57% of its runtime to a
    # cascade before this existed.
    # Whisper's tail inventions are not stable: the same video produced "It's
    # like a mess" on one run, nothing on two others, and "you / the right
    # agent / I like the right agent" on a fourth. No rule about the TEXT can
    # separate those from "Powered by Keller Williams", which is a real
    # sign-off a student actually said - both are short, well formed and
    # absent from the script.
    #
    # The audio can. Invented speech sits over silence. So a trailing segment
    # is checked against the sound underneath it, measured relative to what
    # speech measures in this particular recording.
    rms = ref = None
    if workdir is not None:
        fn, _ = speech_level(workdir)
        if fn is not None:
            spoken = [fn(float(s["start"]), float(s["end"])) for s in segments[:-2]]
            spoken = [v for v in spoken if v > 0]
            if spoken:
                import statistics                              # noqa: PLC0415
                rms, ref = fn, statistics.median(spoken)

    def _silent(seg: dict) -> bool:
        if rms is None or not ref:
            return False
        a, b = float(seg.get("start", 0)), float(seg.get("end", 0))
        if b - a <= 0.05:
            return False                      # the rate check owns these
        return rms(a, b) < ref * 0.30

    tail_dropped: list[dict] = []
    floor = max(3, int(len(segments) * 0.75))

    # A sign-off said twice. The loop detector below needs three occurrences,
    # so a straight doubling at the very end walked past it: 09 finished
    # "Thanks for joining us. Thanks for joining us." A verbatim repeat of the
    # final sentence is Whisper, not a speaker - nobody says their outro twice
    # into the same take.
    def _norm_txt(s: str) -> str:
        return " ".join(re.findall(r"[a-z0-9']+", s.lower()))

    while (len(segments) > floor + 1
           and _norm_txt(segments[-1]["text"])
           and _norm_txt(segments[-1]["text"]) == _norm_txt(segments[-2]["text"])
           and not _in_script(segments[-1]["text"])):
        tail_dropped.insert(0, segments[-1])
        segments = segments[:-1]
    # Silence is decisive and outranks the in-script guard. 04 ended with a
    # hallucinated repeat of "the right agent" - a line that IS in the script,
    # from the middle of it, so being in the script protected an invention.
    # If nothing was said there, it was not said, whatever the words are.
    while (len(segments) > floor
           and (_silent(segments[-1])
                or (not _in_script(segments[-1]["text"])
                    and (_garbage(segments[-1]["text"])
                         or _impossible(segments[-1])
                         or _chatter(segments[-1]["text"]))))):
        tail_dropped.insert(0, segments[-1])
        segments = segments[:-1]

    toks = [_norm(s["text"]) for s in segments]
    tail_from = int(len(segments) * 0.55)        # only the back half is suspect

    # Find a phrase that keeps coming back. Walking backwards pair-by-pair is
    # not enough, because the loop mutates as it goes ("...to find out your
    # door", "...what is part of you", "...going to be people here") and one
    # mismatched pair stops the walk early. Counting a repeated n-gram catches
    # the whole run however much each repetition drifts.
    N = 5
    grams: dict[tuple, list[int]] = {}
    for i in range(tail_from, len(segments)):
        for k in range(len(toks[i]) - N + 1):
            grams.setdefault(tuple(toks[i][k:k + N]), []).append(i)

    cut_from = len(segments)
    for _g, hits in grams.items():
        seen = sorted(set(hits))
        if len(seen) >= 3:                       # same phrase in 3+ segments
            cut_from = min(cut_from, seen[0])

    if cut_from >= len(segments):
        return segments, tail_dropped
    # keep at least the first half of the video whatever happens
    cut_from = max(cut_from, tail_from)
    return segments[:cut_from], segments[cut_from:] + tail_dropped


# A slate: the student counting themselves in before a take. Everything from
# the previous sentence up to and including the slate is a discarded attempt.
SLATE_RE = re.compile(
    r"^\s*(three[,.\s]+two[,.\s]+one|3[,.\s]+2[,.\s]+1|take\s+\w+|"
    r"here we go|one more time|from the top|starting over|let me try again)"
    r"[.!,\s]*$", re.I)


def find_slates(segments: list[dict]) -> list[dict]:
    """Countdown slates, and the failed take each one follows.

    One real upload ran: "The right agent knows the strategy that..." / "Three,
    two, one." / "The right agent..." / "Three, two, one." / "The right agent
    knows the strategies that..." - three attempts at one line with the student
    counting themselves in between them. Every render shipped all three takes
    AND all three countdowns, which is a video nobody could publish.

    Retake detection missed it because the slates sit between the attempts, so
    the repeated sentences are never adjacent. A slate is a much stronger
    signal than text similarity: nobody counts down mid-sentence. Cut the
    slate, and cut back to the start of the attempt it interrupts.
    """
    out = []
    for i, s in enumerate(segments):
        if not SLATE_RE.match(s["text"].strip()):
            continue
        start = s["start"]
        # walk back over the abandoned attempt - a run of segments ending
        # without terminal punctuation, which is how a cut-off take looks
        j = i - 1
        while j >= 0:
            prev = segments[j]["text"].strip()
            if prev.endswith(("...", "-")) or not prev.endswith((".", "?", "!")):
                start = segments[j]["start"]
                j -= 1
                continue
            # A grammatically complete sentence before a slate is still an
            # abandoned take when the line AFTER the slate restates it:
            # "Here's what changes." / slate / "Here's what changes everything."
            # Only the fuller one is wanted, so reach back across the slate.
            nxt = segments[i + 1]["text"] if i + 1 < len(segments) else ""
            if nxt and containment(_norm(prev), _norm(nxt)) >= 0.70:
                start = segments[j]["start"]
            break
        out.append({"s": round(start, 2), "e": round(s["end"], 2),
                    "slate": s["text"].strip()})
    return out


MIN_RETAKE_S = 1.2        # anything shorter is transcriber noise, not a take


def find_retakes(segments: list[dict], thresh: float = 0.80,
                 min_words: int = 4, lookahead: int = 3) -> list[dict]:
    """Segments that restate a nearby segment. Keeps the longer (fuller) take."""
    segs = [{"s": x["start"], "e": x["end"], "t": x["text"].strip(),
             "w": _norm(x["text"])} for x in segments]
    found: dict[tuple, dict] = {}
    for i in range(len(segs)):
        for j in range(i + 1, min(i + 1 + lookahead, len(segs))):
            a, b = segs[i], segs[j]
            if min(len(a["w"]), len(b["w"])) < min_words:
                continue
            # A duration floor as well as a word floor. Whisper emits tiny
            # fragments at the end of a file - two of them 0.02s long - and
            # short fragments trivially "contain" each other, so they scored
            # 1.0 and got cut as retakes. A real retake is a spoken sentence.
            if min(a["e"] - a["s"], b["e"] - b["s"]) < MIN_RETAKE_S:
                continue
            c = containment(a["w"], b["w"])
            if c < thresh:
                continue
            keep, drop = (b, a) if len(b["w"]) >= len(a["w"]) else (a, b)
            k = (drop["s"], drop["e"])
            if k not in found or c > found[k]["containment"]:
                found[k] = {"containment": round(c, 3), "drop": drop, "keep": keep}
    return sorted(found.values(), key=lambda h: h["drop"]["s"])


# --------------------------------------------------------------------------
# 3. keep ranges
# --------------------------------------------------------------------------

PAUSE_TRIM_OVER = 0.70    # a gap longer than this gets tightened
PAUSE_KEEP = 0.26         # ...down to this, so speech still breathes


def find_dead_air(words: list[dict]) -> list[tuple]:
    """Over-long gaps between words, as (start, end) spans to remove.

    Trimming head and tail only was cutting a median of 2.9s from a 55s video,
    while the human editors were taking out two to four times that. The rest of
    the difference is internal: these are recorded off a teleprompter, and the
    pauses between sentences run long. We tighten rather than close them -
    cutting a pause to nothing makes speech sound hurried and unnatural.
    """
    out = []
    for i in range(1, len(words)):
        gap = words[i]["start"] - words[i - 1]["end"]
        if gap > PAUSE_TRIM_OVER:
            out.append((words[i - 1]["end"] + PAUSE_KEEP / 2,
                        words[i]["start"] - PAUSE_KEEP / 2))
    return out


def recover_truncated_tail(workdir: Path, tr: dict, duration: float) -> int:
    """Get back words Whisper dropped off the end.

    Video 10 ends "...so many programs exist to HELP YOU." Transcribing the
    whole file stopped at "to", 4.7 seconds before she finishes, and the cut
    followed it - her last two words were gone from a video the audit passed.
    Transcribing that tail on its own returns them perfectly.

    This is the mirror of the hallucination problem. Whisper invents speech
    past the end of audio, and it also gives up before the end of audio, and
    neither is visible in the transcript itself. So the audio gets the last
    word: if there is still speech-level sound after the final transcribed
    word, that stretch is transcribed again on its own and the words are put
    back.
    """
    segs = tr.get("segments") or []
    words = [w for s in segs for w in s.get("words", [])]
    if not words:
        return 0
    last = float(words[-1]["end"])
    if duration - last < 1.0:
        return 0

    wav = workdir / "audio.wav"
    if not wav.exists():
        return 0
    import wave                                                # noqa: PLC0415
    import numpy as np                                         # noqa: PLC0415
    with wave.open(str(wav), "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    if not len(pcm):
        return 0

    def rms(a: float, b: float) -> float:
        seg = pcm[int(a * sr):int(b * sr)]
        return float(np.sqrt(np.mean(seg.astype(float) ** 2))) if len(seg) else 0.0

    # What speech sounds like in THIS clip, measured on what was transcribed.
    ref = np.median([rms(float(w["start"]), float(w["end"])) for w in words[-40:]])
    if ref <= 0:
        return 0
    loud = [t for t in np.arange(last + 0.15, duration - 0.15, 0.2)
            if rms(t, t + 0.2) > ref * 0.35]
    if len(loud) < 4:                      # under ~0.8s of sound: not a lost line
        return 0

    start = max(0.0, last - 1.5)
    clip = workdir / "tailcheck.wav"
    run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.2f}", "-i", str(wav), "-ac", "1", "-ar", "16000",
         "-y", str(clip)])
    try:
        import mlx_whisper                                     # noqa: PLC0415
        r = mlx_whisper.transcribe(
            str(clip), path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            word_timestamps=True, language="en")
    except Exception:
        return 0
    finally:
        clip.unlink(missing_ok=True)

    extra = []
    for s in r.get("segments", []):
        for w in s.get("words", []):
            ws = float(w["start"]) + start
            if ws > last + 0.05:
                extra.append({**w, "start": ws, "end": float(w["end"]) + start})
    if not extra:
        return 0
    text = "".join(w.get("word", w.get("w", "")) for w in extra)
    segs.append({"start": extra[0]["start"], "end": extra[-1]["end"],
                 "text": text, "words": extra})
    tr["segments"] = segs
    return len(extra)


def build_keep_ranges(words: list[dict], retakes: list[dict], total: float,
                      pad: float = 0.28, trim_pauses: bool = True,
                      slates: list[dict] | None = None) -> list[tuple]:
    """First word to last word, minus retakes and minus over-long pauses."""
    if not words:
        return [(0.0, total)]
    start = max(0.0, words[0]["start"] - pad)
    end = min(total, words[-1]["end"] + pad)

    cuts = [(r["drop"]["s"] - 0.12, r["drop"]["e"] + 0.18) for r in retakes]
    cuts += [(s["s"] - 0.10, s["e"] + 0.15) for s in (slates or [])]
    if trim_pauses:
        cuts += find_dead_air(words)
    cuts = sorted(c for c in cuts if c[1] > c[0])

    keeps, cur = [], start
    for a, b in cuts:
        if b <= cur or a >= end:
            continue
        if a > cur:
            keeps.append((round(cur, 2), round(min(a, end), 2)))
        cur = max(cur, b)
    if cur < end:
        keeps.append((round(cur, 2), round(end, 2)))
    return [(a, b) for a, b in keeps if b - a > 0.25]


def remap(words: list[dict], keeps: list[tuple]) -> tuple[list[dict], float]:
    """Move word timings onto the cut timeline."""
    out, off = [], 0.0
    for idx, (a, b) in enumerate(keeps):
        for w in words:
            if w["start"] >= a - 0.01 and w["end"] <= b + 0.01:
                out.append({"w": w["word"].strip(),
                            "s": w["start"] - a + off,
                            "e": w["end"] - a + off, "seg": idx})
        off += b - a
    return out, off


# --------------------------------------------------------------------------
# 4. captions
# --------------------------------------------------------------------------

# Words a caption must not END on. Greedy 3-word chunking with no sense of
# phrasing produced "and protecting your" / "deposit from day" / "one to keys"
# and left "situations" sitting alone on screen. A caption that ends on "your"
# or "to" reads as a sentence that got cut off, because it is one.
DANGLING = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "from", "by", "into", "than", "then", "as", "if", "so", "because",
    "through", "about", "after", "before", "over", "under", "between",
    "during", "without", "within", "around", "across", "toward", "towards",
    "while", "when", "that", "this", "these", "those", "who", "which", "what",
    "your", "my", "our", "their", "his", "her", "its",
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did",
    "has", "have", "had", "will", "would", "can", "could", "we", "they",
    "he", "she", "i",
    # NOTE: "that" and "this" appear above as determiners ("that home"). They
    # are also legitimate sentence enders ("buyers approach this."), which is
    # why the check below is against the sentence boundary, not this set
    # alone.
}


# "Don't let this be you." is a whole sentence that happens to end on a
# pronoun, and the first version of this rule failed seven of ten videos for
# it. These hang only MID-sentence, so they are judged against where the
# sentence actually ends rather than banned outright.
OBJECT_PRONOUNS = {"you", "it", "them", "us", "me", "him", "her",
                   "that", "this"}


# Rendered at 86px with 140px margins in a 1080 frame, a caption fits to about
# 32 characters and clips beyond it. Measured, not guessed: 27 chars sits
# comfortably, 32 touches both edges, 34 and 36 are cut off at both ends.
# Letting captions run to five words produced "get accepted, navigating
# inspections" - 36 characters, and on screen it read "t accepted, navigating
# inspectio".
# Caption width is measured, not counted. Character count is a bad proxy:
# "The seller's agent only" and "crushed in negotiations" are both 23
# characters, and the first fits on one line while the second wraps, because
# "negotiations" has far wider glyphs. Measured with Helvetica Neue Bold at
# 86px against what libass actually did: 943px stayed on one line, 962px
# wrapped. A wrapped caption is not merely untidy - the second line grows
# UPWARD, and on 04 it put "crushed in" directly across the speaker's mouth.
# The caption face ships WITH the skill and is never taken from the system.
# The style used to name "Helvetica Neue", which exists on macOS and nowhere
# else: on Windows or Linux libass silently substitutes some other face, and
# the width measurement - calibrated against Helvetica Neue's metrics - was
# then measuring a font that was not on screen. Worse, with no Helvetica Neue
# to load, the measurement fell back to a flat per-character estimate that is
# up to 80% wrong ("illi illi illi illi" 402px measured, 722px estimated).
# Captions would wrap or clip on every non-Mac machine.
#
# Barlow Bold (SIL Open Font License, in assets/) is bundled instead, handed
# to libass with fontsdir and to the measurement as the same file, so the
# glyphs on screen and the glyphs being measured are the same ones on every
# platform.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
CAPTION_FONT_FILE = ASSETS_DIR / "Barlow-Bold.ttf"
CAPTION_FONT_NAME = "Barlow"


def _esc_filter_path(p: Path) -> str:
    r"""A path safe inside an ffmpeg filtergraph argument.

    Windows drive letters are the reason this exists: C:\Users\... carries
    both a colon and backslashes, which the filtergraph parser treats as
    option and escape characters.

    The counts below are not a reading of the documentation, they are what
    ffmpeg actually accepts, found by rendering a one-frame clip against a
    directory named for each character in turn. The first version of this
    function escaped `:` with a single backslash, which reads correctly and
    does not work: ffmpeg still split the argument there and reported
    `Unable to parse option value "colon/t.ass"`. Since a Windows path
    ALWAYS contains a drive colon, that was every Windows render, every
    time - and the test that would have caught it could not be written on a
    Mac until the path was put in a directory name rather than a drive.

    The three tiers are three parsers in a row. The filtergraph splits on
    `, ; [ ]`, the filter then splits its own arguments on `:` and honours
    `'`, and each layer consumes one level of backslash on the way past.
    """
    s = str(p).replace("\\", "/")        # Windows separators first
    out = []
    for ch in s:
        if ch == "'":
            out.append("\\\\\\'")        # survives all three
        elif ch == ":":
            out.append("\\\\:")          # argument separator
        elif ch in ",;[]":
            out.append("\\" + ch)        # filtergraph separators
        else:
            out.append(ch)
    return "".join(out)


CAPTION_LINE_PX = 95          # one line of 80pt type plus its outline
CAPTION_GAP_PX = 25           # breathing room under the chin
CAPTION_PT = 80               # was 86 - see below
CAPTION_MARGIN_LR = 80        # was 140
# Re-measured for the bundled face. Barlow Bold is about 6% narrower than the
# Helvetica Neue these were first calibrated against, so more fits per line:
# rendered through the real filter, 1229px sits on one line and 1294px wraps.
MAX_CAPTION_PX = 1150         # comfortable
HARD_CAPTION_PX = 1270        # measured wrap point
_FONT_CACHE: list = []


def text_width(s: str, size: int = CAPTION_PT) -> float:
    """Rendered width of a caption, in output pixels."""
    if not _FONT_CACHE:
        try:
            from PIL import ImageFont                          # noqa: PLC0415
            _FONT_CACHE.append(ImageFont.truetype(str(CAPTION_FONT_FILE), size))
        except Exception:
            _FONT_CACHE.append(None)
    f = _FONT_CACHE[0]
    if f is None:
        return len(s) * 38.0          # rough fallback, calibrated on the above
    return float(f.getlength(s))


def _bare(w: str) -> str:
    return re.sub(r"[^a-z']", "", w.lower())


def build_cues(mapped: list[dict], max_words: int = 5) -> list[dict]:
    """Split speech into captions by choosing the break points, not grabbing
    three words at a time and patching what falls out.

    The greedy version was fixed five times and each fix produced the next
    fault: three words at a time left "situations" alone on screen; extending
    to escape that produced "get accepted, navigating inspections", which is
    36 characters and rendered as "t accepted, navigating inspectio"; capping
    the width put the orphans back; merging orphans forward ran two sentences
    into one caption. They are not separate bugs. They are one decision -
    where to break - being made one word at a time with no view of the rest of
    the sentence.

    So: inside each run of words that belongs together (one sentence, one cut,
    no real pause), every possible split is scored and the cheapest wins.
    A caption wants to be around 22 characters, must not exceed the frame,
    must not sit alone, and must not stop on a word that needs the next one.
    """
    merged: list[dict] = []
    for w in mapped:
        if merged and len(w["w"]) > 1 and w["w"].startswith("-"):
            merged[-1]["w"] += w["w"]
            merged[-1]["e"] = w["e"]
        elif merged and re.fullmatch(r"[%,.?!]+", w["w"]):
            merged[-1]["w"] += w["w"]
            merged[-1]["e"] = w["e"]
        else:
            merged.append(dict(w))

    # split into runs that must not be spanned
    runs: list[list[dict]] = []
    cur: list[dict] = []
    for k, w in enumerate(merged):
        cur.append(w)
        nxt = merged[k + 1] if k + 1 < len(merged) else None
        if (nxt is None or re.search(r"[.?!]$", w["w"])
                or nxt["s"] - w["e"] > 0.45 or nxt["seg"] != w["seg"]):
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    BIG = 1000.0

    def chunk_cost(ws: list[dict], is_tail: bool) -> float:
        txt = " ".join(x["w"] for x in ws).rstrip(".,")
        n = len(ws)
        w = text_width(txt)
        c = abs(w - 1050) / 4.0                     # aim for a comfortable width
        if w > HARD_CAPTION_PX:
            c += BIG * (w - HARD_CAPTION_PX) / 10.0 + BIG
        elif w > MAX_CAPTION_PX:
            c += (w - MAX_CAPTION_PX) / 2.0
        if n == 1:
            # Three times the dangling penalty, not equal to it. "Here's what
            # changes everything" is 1222px - too wide for one caption - and
            # its only even split stops on "what". Both faults scored the same
            # and the orphan won by a hair, leaving "everything" alone on
            # screen in three of the ten videos. A word on its own reads as a
            # mistake; "Here's what / changes everything" merely reads a beat
            # early.
            c += BIG * 3
        last = _bare(ws[-1]["w"])
        ends_sentence = bool(re.search(r"[.?!]$", ws[-1]["w"]))
        if not ends_sentence and (last in DANGLING
                                  or (last in OBJECT_PRONOUNS and not is_tail)):
            c += BIG
        return c

    cues: list[dict] = []
    for run in runs:
        n = len(run)
        best = [0.0] + [float("inf")] * n
        back = [0] * (n + 1)
        for i in range(1, n + 1):
            for size in range(1, max_words + 1):
                j = i - size
                if j < 0 or best[j] == float("inf"):
                    continue
                c = best[j] + chunk_cost(run[j:i], is_tail=(i == n))
                if c < best[i]:
                    best[i], back[i] = c, j
        cuts, i = [], n
        while i > 0:
            cuts.append((back[i], i))
            i = back[i]
        forced = best[n] >= BIG      # every split of this run carried a fault
        for a, b in reversed(cuts):
            grp = run[a:b]
            txt = " ".join(g["w"] for g in grp)
            txt = re.sub(r"\s+([%,.?!])", r"\1", txt).rstrip(".,")
            cues.append({"s": grp[0]["s"], "e": grp[-1]["e"] + 0.12, "t": txt,
                         "eos": bool(re.search(r"[.?!]$", grp[-1]["w"])),
                         "forced": forced})

    for j in range(len(cues) - 1):         # close gaps so captions never flicker
        cues[j]["e"] = min(cues[j + 1]["s"], cues[j]["e"] + 0.30)
    return cues


# Where the speaker's mouth sits over the cut, in output pixels, filled in by
# caption_margin(). One margin for a whole video is wrong when the framing
# changes partway through: on video 09 she is small and low for thirty
# seconds and then large and close, and a single height that cleared the first
# half sat on her chin through the second.
CAPTION_SAMPLES: list[tuple[float, float]] = []


def caption_margin(src: str, model: str, keeps: list[tuple], work: Path,
                   zooms: list[float], trf: str | None = None,
                   default: int = 470) -> int:
    """Place captions below the mouth, measured on a REAL rendered probe.

    Four attempts modelled this from the source and applied the punch crop,
    then the stabiliser's optzoom, as correction factors. Every one was wrong -
    the last by 617px, which put captions across the speaker's face. The
    transforms compound in ways that are not worth predicting.

    So: render two seconds through the actual video chain, measure that, and
    use the number. It costs one short ultrafast encode and it cannot drift
    from the real geometry, because it IS the real geometry.

    Targets the median frame. For a speaker who fills the frame, clearing even
    the 75th percentile needs a margin inside the platform's UI strip, so the
    typical frame is the most that can be honoured.
    """
    try:
        import cv2                                             # noqa: PLC0415
        import numpy as np                                     # noqa: PLC0415
    except ImportError:
        return default
    if not os.path.exists(model) or not keeps:
        return default

    # Probe the WHOLE cut, at a quarter size. Sampling nine windows missed
    # how much this speaker moves: the probe read a median mouth of 877px
    # where the finished video's was 1448px, and captions landed across her
    # mouth. A 270x480 ultrafast pass over the full runtime costs a few
    # seconds and cannot be unrepresentative.
    z = zooms[0] if zooms else 0.94
    probe = work / "probe.mp4"
    # Stabilise ONCE and split, exactly as the final render does. Repeating
    # vidstabtransform inside each trim branch makes ffmpeg decode and
    # stabilise the whole source once per branch and throw away all but one
    # slice. 04 cuts into four segments once its three slates are removed, so
    # the probe was doing 4x the work - 7+ minutes for a 48s video, and
    # getting worse with every extra cut. Same output, same chain, one pass.
    parts, labels = [], []
    if trf:
        parts.append(f"[0:v]vidstabtransform=input={trf}:smoothing=20:optzoom=1,"
                     f"split={len(keeps)}"
                     + "".join(f"[s{i}]" for i in range(len(keeps))))
    for i, (a, b) in enumerate(keeps):
        parts.append(f"[{'s'+str(i) if trf else '0:v'}]trim={a}:{b},setpts=PTS-STARTPTS,"
                     f"crop=iw*{z}:ih*{z}:(iw-iw*{z})/2:(ih-ih*{z})*0.32,"
                     f"scale={W // 4}:{H // 4},setsar=1[p{i}]")
        labels.append(f"[p{i}]")
    chain = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(keeps)}:v=1:a=0[v]"
    try:
        run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", src,
             "-filter_complex", chain, "-map", "[v]", "-an",
             "-c:v", "libx264", "-crf", "40", "-preset", "ultrafast",
             "-y", str(probe)])
    except subprocess.CalledProcessError:
        return default

    det = cv2.FaceDetectorYN.create(model, "", (320, 320), 0.6, 0.3, 5000)
    cap = cv2.VideoCapture(str(probe))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    pfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    mouths = []
    timed: list[tuple[float, float]] = []       # (seconds, mouth y in output px)
    for k in range(0, n, max(1, n // 120)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, k)
        ok, fr = cap.read()
        if not ok:
            continue
        det.setInputSize((fr.shape[1], fr.shape[0]))
        _, faces = det.detect(fr)
        ff = faces if faces is not None else []
        if not len(ff):
            continue
        # the speaker is the largest face; scale back to full frame height
        big = max(ff, key=lambda x: x[2] * x[3])
        # The CHIN, not the mouth. Aiming 90px below the mouth line still
        # lands on the face: the chin is the bottom 22% of the box, which is
        # about 90px on a typical framing, so the two cancel out exactly.
        # Jerry spotted it on a cut that had passed 17/17, and measuring all
        # three speakers showed every one of them overlapping by 85-155px.
        my = float(big[1] + big[3]) * 4.0
        mouths.append(my)
        timed.append((k / pfps, my))
    cap.release()
    probe.unlink(missing_ok=True)
    CAPTION_SAMPLES.clear()
    CAPTION_SAMPLES.extend(timed)
    if not mouths:
        return default
    mouths.sort()
    import numpy as _np                                        # noqa: PLC0415
    # Aim to clear the 90th percentile, not the median. Sitting at the median
    # is the worst of both worlds: it guarantees the caption crosses the face
    # in half the frames. If clearing p90 would push the caption into the
    # bottom 220px UI strip - which it does for a speaker framed tight, where
    # p90 sits at 1662px - drop to the lowest legal position instead of
    # splitting the difference. Low and legal beats centred and wrong.
    p90 = float(_np.percentile(mouths, 90))
    want = H - p90 - CAPTION_LINE_PX - CAPTION_GAP_PX
    if want < 240:
        return 240     # 240 keeps the caption clear of the bottom UI strip
    return max(240, min(620, int(want)))


def accent_color(video: str, keeps: list[tuple]) -> str:
    """One signal colour, sampled from the speaker's own footage.

    headliner-cut rule 3: pick the accent out of something already on screen -
    wardrobe, backdrop, the light in the room - so the graphics look like they
    belong to the footage instead of sitting on top of it. Returns an ASS
    colour string (&HBBGGRR&).
    """
    try:
        import cv2                                             # noqa: PLC0415
        import numpy as np                                     # noqa: PLC0415
    except ImportError:
        return "&H3A3AE8&"                                     # a safe red
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return "&H3A3AE8&"
    best, best_score = None, -1.0
    for a, b in keeps[:2]:
        for f in (0.3, 0.6):
            cap.set(cv2.CAP_PROP_POS_MSEC, (a + (b - a) * f) * 1000)
            ok, fr = cap.read()
            if not ok:
                continue
            small = cv2.resize(fr, (64, 114))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            s, v = hsv[:, :, 1].astype(float), hsv[:, :, 2].astype(float)
            mask = (s > 90) & (v > 80) & (v < 245)             # vivid, not blown out
            if mask.sum() < 40:
                continue
            score = float(mask.mean())
            if score > best_score:
                best_score = score
                px = small.reshape(-1, 3)[mask.reshape(-1)]
                best = np.median(px, axis=0)                   # BGR
    cap.release()
    if best is None:
        return "&H3A3AE8&"
    # Take the HUE from the footage, but force saturation and brightness to a
    # vivid level. Sampling raw gave a muted mauve off a plaid shirt that read
    # as LESS visible than the white it was meant to pick out - an accent that
    # recedes is worse than no accent. Hue keeps it belonging to the shot;
    # the punch has to be imposed.
    px = np.uint8([[[int(best[0]), int(best[1]), int(best[2])]]])
    h_, _s, _v = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0][0]
    vivid = cv2.cvtColor(np.uint8([[[h_, 235, 250]]]), cv2.COLOR_HSV2BGR)[0][0]
    b, g, r = (int(c) for c in vivid)
    return f"&H{b:02X}{g:02X}{r:02X}&"


# A keyword is worth highlighting when it carries the claim: a number, a sum of
# money, or a word the triggers already treat as concrete.
NUMBER_RE = re.compile(r"^\$?[\d][\d,.]*%?$")
KEYWORD_RE = re.compile(
    r"^\$?[\d][\d,.]*%?$|^(never|always|free|zero|every|most|biggest|worst|"
    r"best|only|mistake|mistakes|critical|must|avoid)$", re.I)


# No `font` parameter: the style below names CAPTION_FONT_NAME and the file is
# pinned with fontsdir, so a caller cannot ask for a face that is not bundled.
# It used to default to "Helvetica Neue", which no caller passed and the style
# no longer read - a dead knob advertising the exact behaviour this fixed.
def write_ass(cues: list[dict], path: Path,
              margin_v: int = 470, accent: str = "&H3A3AE8&") -> None:
    ts = lambda t: f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{CAPTION_FONT_NAME},{CAPTION_PT},&H00FFFFFF,&HC0000000,&HA0000000,1,0,1,3,4,2,{CAPTION_MARGIN_LR},{CAPTION_MARGIN_LR},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    pop = r"{\fscx92\fscy92\t(0,110,\fscx100\fscy100)}"

    def mark(text: str) -> str:
        """Colour at most ONE word per phrase - the audit checklist calls for
        'exactly one accent coloured keyword per phrase', and more than one
        stops the eye finding the claim."""
        words = text.split()
        # A number beats a word. "yet only 16%" accented "only", because the
        # scan took the first match and "only" came first - but the number is
        # the concrete claim and the word is just emphasis around it. Look for
        # a figure first, fall back to the word list.
        order = ([i for i, w in enumerate(words) if NUMBER_RE.match(w.strip(".,!?"))]
                 + [i for i, w in enumerate(words) if KEYWORD_RE.match(w.strip(".,!?"))])
        for i in order:
            words[i] = (r"{\c" + accent + "}" + words[i] + r"{\c&HFFFFFF&}")
            break
        return " ".join(words)

    def local_margin(c: dict) -> int:
        """Caption height for THIS caption, from the mouth near this moment.

        The Dialogue line carries its own MarginV, which overrides the style,
        so this costs nothing at render time. Samples are taken a little wider
        than the caption so a head that is moving does not get clipped.
        """
        near = [m for s, m in CAPTION_SAMPLES if c["s"] - 0.7 <= s <= c["e"] + 0.7]
        if not near:
            return margin_v
        import numpy as _np                                   # noqa: PLC0415
        # p75 of the local window, not the max: one frame of a lean should not
        # shove the caption down for the whole phrase.
        low = float(_np.percentile(near, 75))
        return max(240, min(620, int(H - low - CAPTION_LINE_PX - CAPTION_GAP_PX)))

    for c in cues:                   # keep it, the report needs it
        c["margin"] = local_margin(c)
    body = "".join(
        f"Dialogue: 0,{ts(c['s'])},{ts(c['e'])},Cap,,0,0,{c['margin']},,"
        f"{pop}{mark(c['t'])}\n"
        for c in cues)
    path.write_text(head + body)


# --------------------------------------------------------------------------
# 5. b-roll
# --------------------------------------------------------------------------

# The twelve standard scripts, and the categories that suit each. Detecting
# which script a video is lets us constrain b-roll to categories that fit the
# topic, instead of letting a single trigger word anywhere in the sentence pull
# a clip that happens to match a noun. See reference/script-broll-map.md.
SCRIPTS = [
    ("01 Why Hire A Realtor",       r"why (hire|you need).*realtor|need a realtor|discount broker",
     ["documents", "keys", "money_growth"]),
    ("02 How Quickly Will It Sell", r"how quickly|how (long|fast).*(sell|market)|days on market",
     ["money_growth", "curb_exterior", "documents"]),
    ("03 Thinking About Buying",    r"thinking (about|of) buying",
     ["keys", "curb_exterior", "money_cash"]),
    ("04 Curb Appeal",              r"curb appeal|first impression|maximize.*value",
     ["curb_prep", "lawn", "curb_exterior", "interior_staged"]),
    ("05 Digital Marketing",        r"digital marketing|social media|video marketing",
     ["marketing_video", "curb_exterior"]),
    ("06 Approval vs Pre-Qual",     r"pre.?qual|pre.?approv|approval vs",
     ["documents", "money_cash"]),
    ("07 Offer Accepted",           r"offer accepted|get your offer",
     ["documents", "keys"]),
    ("08 Pre-Listing Inspection",   r"pre.?listing inspection|get an inspection",
     ["inspection", "flooring", "interior_staged"]),
    ("09 What To Expect Buying",    r"what to expect when buying",
     ["documents", "keys", "curb_exterior"]),
    ("10 How Much Money",           r"how much money|down payment|out of pocket",
     ["money_cash", "money_growth", "documents"]),
    ("11 10 Must-Nots",             r"must.?nots?|don'?t do (this|these)",
     ["documents", "money_cash", "keys"]),
    ("12 Will It Pass The Test",    r"pass the test|appraisal|inspection pass",
     ["inspection", "flooring", "interior_staged"]),
]


def detect_script(text: str, title: str | None = None) -> tuple[str | None, list[str]]:
    """Which of the twelve standard scripts this is.

    Prefer the TITLE, because that is what actually identifies the script.
    Matching the spoken words was the mistake: nobody says the title out loud.
    The "Why Hire A Realtor" video never utters that phrase, it says "expert
    guidance through finding homes" - so title-matching against audio scored
    3 of 12. The student knows which script they just recorded, and Cut-Q
    already stores it, so it should be passed in, not inferred.

    Falls back to the transcript when no title is given, which is better than
    nothing but should not be relied on. (None, []) means no constraint.
    """
    if title:
        tl = title.lower()
        for name, pat, cats in SCRIPTS:
            if re.search(pat, tl):
                return name, cats
    t = text.lower()
    best, best_n = None, 0
    for name, pat, cats in SCRIPTS:
        n = len(re.findall(pat, t))
        if n > best_n:
            best, best_n = (name, cats), n
    return (best[0], best[1]) if best else (None, [])


PICK_STATS: dict = {}
# Order to fall back through when a floor slot has no on-topic category left.
CATEGORY_FALLBACK = ["curb_exterior", "documents", "money_cash", "keys",
                     "interior_staged", "money_growth", "curb_prep", "lawn",
                     "inspection", "flooring", "marketing_video"]
SLOT_EVERY_S = 11.0       # aim for roughly one insert per this many seconds
SLOT_MIN, SLOT_MAX = 3, 7
MIN_GAP_S = 5.0


def pick_slots(mapped: list[dict], total: float, dur: float = 3.6,
               allowed: list[str] | None = None) -> list[dict]:
    """Score every possible insert, then spread a consistent number of them.

    The old version took the first trigger that matched and moved on, which
    produced two inserts in one video and six in another of the same length,
    and let a weak word win just by appearing earlier. Now every candidate is
    scored and the timeline is divided into even bands, with the best candidate
    taken from each band - so density is predictable and the strongest cue in
    each stretch wins rather than the earliest.
    """
    # Protections scale with runtime. Fixed 4s + 5.5s + 2s was tuned on a ~55s
    # video; on a 29s one it fences off 40% of the timeline and the 3-insert
    # floor becomes unreachable through no fault of the picker.
    # Never below 3.0s: RULES.md states no insert in the first 3 seconds,
    # and scaling this with runtime once let an insert land at 2.63s on a
    # 29s video. The audit caught the code disagreeing with its own rule.
    hook_s = min(HOOK_PROTECT_S, max(3.0, total * 0.09))
    intro_s = min(5.0, max(2.5, total * 0.10))
    protected = [(0.0, hook_s), (total - min(2.0, total * 0.05), total)]
    intro = [w for w in mapped if INTRO_RE.search(w["w"])]
    if intro:
        protected.append((intro[0]["s"] - 0.5, intro[0]["s"] + intro_s))

    def blocked(a, b):
        return any(not (b <= p0 or a >= p1) for p0, p1 in protected)

    # ---- 1. every candidate, scored ----
    cands = []
    for i, w in enumerate(mapped):
        window = mapped[i:i + 6]
        ctx = " ".join(x["w"] for x in window).lower()
        before = " ".join(x["w"] for x in mapped[max(0, i - 4):i]).lower()
        a, b = w["s"], min(w["s"] + dur, total - 1.5)
        if b - a < 2.5 or blocked(a, b):
            continue
        for pat, kind, weight in TRIGGERS:
            m = re.search(pat, ctx)
            if not m:
                continue
            # Hard filter, not a preference. A -1.0 penalty still let a
            # toolbox into a mortgage-qualification video, because the speaker
            # says the word "inspections" in passing and the trigger fired on
            # it. A cue firing is not the same as the clip belonging.
            #
            # This was a filter once before and starved the picker. The
            # difference now is the floor: cues stay on-topic for relevance,
            # and the floor tops up to three using the script's own categories
            # for density. Relevance and density are solved separately instead
            # of trading against each other.
            if allowed and kind not in allowed:
                continue
            score = float(weight)
            # the cue should be at the START of the insert, not trailing out of it
            hit_at = len(ctx[:m.start()].split())
            score -= 0.35 * hit_at
            # "don't spend your closing money" is not a cue to show keys
            if NEGATION.search(before + " " + ctx[:m.start()]):
                score -= 2.0
            if score <= 0:
                continue
            cands.append({"start": round(a, 2), "end": round(b, 2), "kind": kind,
                          "score": round(score, 2),
                          "over": " ".join(x["w"] for x in window)})
            break                      # one candidate per word position

    PICK_STATS['candidates'] = len(cands)
    PICK_STATS['by_kind'] = {k: sum(1 for c in cands if c['kind'] == k)
                             for k in {c['kind'] for c in cands}}
    if not cands:
        return []

    # ---- 2. even bands across the timeline, best candidate per band ----
    target = max(SLOT_MIN, min(SLOT_MAX, round(total / SLOT_EVERY_S)))
    lo = hook_s
    hi = max(lo + 1.0, total - min(2.0, total * 0.05))
    band = (hi - lo) / target

    def best_of(pool, chosen):
        recent = [s["kind"] for s in chosen[-2:]]
        pool.sort(key=lambda c: (-c["score"], c["kind"] in recent, c["start"]))
        return pool[0]

    def free(c, chosen):
        if not all(abs(c["start"] - s["start"]) >= MIN_GAP_S for s in chosen):
            return False
        # headliner-cut's collision scan: a repeat reads as "she only had three
        # clips". Never place the same category adjacent to itself in time.
        nearest = min(chosen, key=lambda s: abs(s["start"] - c["start"]),
                      default=None)
        return not (nearest and nearest["kind"] == c["kind"])

    chosen: list[dict] = []
    for k in range(target):
        b0, b1 = lo + k * band, lo + (k + 1) * band
        pool = [c for c in cands if b0 <= c["start"] < b1 and free(c, chosen)]
        if pool:
            chosen.append(best_of(pool, chosen))

    # A band with no candidate must not silently cost us an insert. Without
    # this, target behaved as a ceiling and a 58s video came out with one
    # insert instead of five - the inconsistency the banding was meant to fix.
    # Fill from anywhere that still respects the minimum spacing.
    while len(chosen) < target:
        pool = [c for c in cands
                if free(c, chosen) and not any(c["start"] == s["start"] for s in chosen)]
        if not pool:
            break
        chosen.append(best_of(pool, chosen))

    PICK_STATS['from_cues'] = len(chosen)

    # ---- 3. the floor is a RULE, not a target ----
    #
    # Everything above depends on a trigger word firing, and the scripts are
    # largely about process - comparables, terms, qualification - which no
    # concrete noun covers. A 33s video about negotiating an offer produced
    # six candidates, all from the bare word "home", and shipped with ONE
    # insert. Chasing that through the vocabulary is open-ended; the floor is
    # not negotiable, so it is enforced here instead of hoped for.
    #
    # These placements are chosen by POSITION rather than by meaning, so they
    # are deliberately drawn from the script's own categories - a generically
    # on-topic clip beats an empty stretch of talking head.
    floor = int(os.environ.get('CLEANCUT_SLOT_MIN', SLOT_MIN))
    if len(chosen) < floor:
        # ONLY the script's own categories. The fallback list used to widen to
        # every category once the script's ran out, which put a toolbox in a
        # mortgage-qualification video - the floor forcing an insert mattered
        # more to the code than the insert being about the subject. It should
        # never have passed, and no audit rule caught it because none existed.
        prefer = list(allowed) if allowed else list(CATEGORY_FALLBACK)
        guard = 0
        while len(chosen) < floor and guard < 60:
            guard += 1
            # widest remaining stretch of unprotected timeline
            edges = sorted([lo] + [s["start"] for s in chosen]
                           + [s["end"] for s in chosen] + [hi])
            spans = [(edges[i], edges[i + 1]) for i in range(0, len(edges) - 1)]
            spans = [(a, b) for a, b in spans if b - a >= dur + 0.6]
            spans.sort(key=lambda ab: ab[1] - ab[0], reverse=True)
            placed = False
            for a0, b0 in spans:
                # Sweep the whole span, not just its midpoint. The midpoint of
                # the one usable gap kept landing inside the speaker's name
                # reveal, and a single blocked probe made the whole floor give
                # up - which is how a video shipped with one insert while the
                # floor said three.
                room = b0 - a0 - dur
                probes = sorted({round(a0 + room * f, 2)
                                 for f in (0.5, 0.25, 0.75, 0.1, 0.9, 0.0, 1.0)})
                for at in probes:
                    if blocked(at, at + dur):
                        continue
                    cand = {"start": at, "end": round(at + dur, 2), "kind": "",
                            "score": 0.0, "over": "(placed to meet the 3-insert floor)"}
                    for kind in prefer:
                        cand["kind"] = kind
                        if free(cand, chosen):
                            chosen.append(dict(cand))
                            placed = True
                            break
                    if placed:
                        break
                if placed:
                    break
            if not placed:
                break

    PICK_STATS['target'] = target
    PICK_STATS['floor'] = floor
    PICK_STATS['chosen'] = len(chosen)
    return sorted(chosen, key=lambda c: c["start"])


def library_clip(broll_dir: Path, kind: str, used: set) -> str | None:
    """Next unused clip of this kind. Library layout: <broll_dir>/<kind>/*.mp4

    Everything in the library is assumed already face-screened by
    screen_broll.py. Nothing unscreened should ever be in there.
    """
    d = broll_dir / kind
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.mp4")):
        if str(p) not in used:
            used.add(str(p))
            return str(p)
    return None


def make_card_mask(path: Path) -> None:
    from PIL import Image, ImageDraw                          # noqa: PLC0415
    m = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, CARD_W - 1, CARD_H - 1],
                                        radius=CARD_R, fill=255)
    img = Image.new("RGBA", (CARD_W, CARD_H), (255, 255, 255, 0))
    img.putalpha(m)
    img.save(path)


# --------------------------------------------------------------------------
# 6. render
# --------------------------------------------------------------------------

SHAKE_MEAN_TRIGGER = 0.50     # px/frame at 320x568, averaged
SHAKE_P95_TRIGGER = 1.50


def measure_shake(path: str, max_frames: int = 400) -> dict | None:
    """Frame-to-frame global motion, via phase correlation.

    vidstab writes a binary .trf we cannot read back, so shake is measured
    directly. Numbers are px at a 320x568 working size: a tripod reads about
    0.2, a hand-held phone about 1.3.
    """
    try:
        import cv2                                             # noqa: PLC0415
        import numpy as np                                     # noqa: PLC0415
    except ImportError:
        return None
    cap = cv2.VideoCapture(path)
    prev, d = None, []
    while len(d) < max_frames:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(fr, (320, 568)), cv2.COLOR_BGR2GRAY).astype("float32")
        if prev is not None:
            (dx, dy), _ = cv2.phaseCorrelate(prev, g)
            d.append((dx * dx + dy * dy) ** 0.5)
        prev = g
    cap.release()
    if not d:
        return None
    a = np.array(d)
    return {"mean_px": round(float(a.mean()), 2),
            "p95_px": round(float(np.percentile(a, 95)), 2),
            "frames": len(a)}


def stabilize_pass1(src: str, work: Path) -> str | None:
    """Run vidstabdetect and return the transform file, or None if steady.

    Conditional on purpose. Stabilising already-steady footage costs a crop and
    some sharpness for no benefit, and two of the four test videos were shot on
    something solid (0.18-0.21 px/frame).
    """
    s = measure_shake(src)
    if not s:
        return None
    forced = os.environ.get("CLEANCUT_FORCE_STAB") == "1"
    if not forced and s["mean_px"] < SHAKE_MEAN_TRIGGER and s["p95_px"] < SHAKE_P95_TRIGGER:
        print(f"      camera steady (mean {s['mean_px']}px) - no stabilisation")
        return None
    trf = work / "stab.trf"
    print(f"      camera shake mean {s['mean_px']}px / p95 {s['p95_px']}px "
          f"-> stabilising")
    try:
        run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", src,
             "-vf", f"vidstabdetect=shakiness=8:accuracy=15:stepsize=6:result={trf}",
             "-f", "null", "-"])
    except subprocess.CalledProcessError:
        return None
    return str(trf) if trf.exists() else None


def measure_loudness(path: str, af: str) -> float | None:
    err = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-af", f"{af},ebur128=framelog=quiet",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.findall(r"I:\s*(-?[\d.]+) LUFS", err)
    return float(m[-1]) if m else None


def render(src: str, out: str, keeps: list[tuple], slots: list[dict],
           clips: list[str], ass: Path, mask: Path, zooms: list[float],
           trf: str | None = None) -> None:
    # No -noautorotate here. filter_complex applies the display matrix on its
    # own; suppressing it and transposing by hand made ffmpeg write the flag to
    # the OUTPUT, so players rotated an already-upright frame. The fix for
    # "plays sideways" was to stop interfering, not to interfere harder.
    ins = ["-i", src, "-i", str(mask)]
    for c in clips:
        ins += ["-i", c]
    for s in slots:
        ins += ["-f", "lavfi", "-i",
                f"color=white:s={W}x{H}:d={s['end'] - s['start'] + 0.3}"]

    n_clip = len(clips)
    fc: list[str] = []
    # Stabilise once on the whole source, then cut from the stabilised stream:
    # the .trf is indexed by source frame, so it has to come before any trim.
    if trf:
        fc.append(f"[0:v]vidstabtransform=input={trf}:smoothing=20:optzoom=1:"
                  f"interpol=bicubic,unsharp=5:5:0.3,split={len(keeps)}"
                  + "".join(f"[src{i}]" for i in range(len(keeps))))
    for n, (a, b) in enumerate(keeps):
        z = zooms[min(n, len(zooms) - 1)]
        fc.append(
            f"[{'src'+str(n) if trf else '0:v'}]trim={a}:{b},setpts=PTS-STARTPTS,"
            f"crop=iw*{z}:ih*{z}:(iw-iw*{z})/2:(ih-ih*{z})*0.32,"
            f"scale={W}:{H}:flags=lanczos,setsar=1[v{n}]")
        # RMS-match each speech segment to a common target before mixing.
        # build-mix.py calls unmatched speech "the number one reason a cut
        # sounds homemade" - phone audio and a stage mic run 8dB apart, and a
        # single global loudnorm at the end cannot even them out.
        fc.append(f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,"
                  f"dynaudnorm=f=250:g=5:p=0.9:m=8:r=0.9[a{n}]")
    fc.append("".join(f"[v{n}][a{n}]" for n in range(len(keeps)))
              + f"concat=n={len(keeps)}:v=1:a=1[vc][ac]")
    fc.append("[vc]unsharp=5:5:0.5:5:5:0.0,eq=contrast=1.04:saturation=1.05[vbase]")

    cur = "vbase"
    for n, s in enumerate(slots[:n_clip]):
        vi, wi = n + 2, 2 + n_clip + n
        a, b = s["start"], s["end"]
        dur = b - a
        # Fill the portrait card edge to edge. A letterboxed clip with blurred
        # bands above and below reads as a mistake, and the delivered videos we
        # measured are full-bleed portrait throughout, so match that.
        #
        # A portrait source just covers. A landscape source is centre-cropped,
        # which is why the library wants close-ups with a centred subject -
        # cash, keys, a brush, a texture - rather than wide establishing shots.
        # Slow push on every insert, with NO upper clamp. A capped zoom was
        # worse than none: it reached its limit two-thirds of the way through
        # and the remaining 0.9s was a constant zoom on a still clip, which is
        # a literal freeze. All four still runs the audit found sat at insert
        # ends for exactly that reason.
        # Stock clips are frequently near-still -
        # one in the library moves 0.46 units a frame - and a still insert
        # registers as a freeze: the audit measured a 0.6s dead stretch inside
        # one. A gentle zoom guarantees motion whatever the source does, and it
        # reads as intentional camera work rather than a patch.
        fc.append(f"[{vi}:v]trim=0:{dur},setpts=PTS-STARTPTS,"
                  f"scale={CARD_W*2}:{CARD_H*2}:force_original_aspect_ratio=increase,"
                  f"crop={CARD_W*2}:{CARD_H*2},"
                  f"zoompan=z='1.0+0.0016*on':d=1:"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                  f"s={CARD_W}x{CARD_H}:fps=30,setsar=1,format=rgba[cc{n}]")
        fc.append(f"[1:v]scale={CARD_W}:{CARD_H},format=rgba,alphaextract[m{n}]")
        fc.append(f"[cc{n}][m{n}]alphamerge[card{n}]")
        fc.append(f"[{wi}:v][card{n}]overlay=(W-w)/2:(H-h)/2:format=auto,"
                  f"setpts=PTS+{a}/TB[plate{n}]")
        fc.append(f"[{cur}][plate{n}]overlay=0:0:enable='between(t,{a},{b})':"
                  f"eof_action=pass[ov{n}]")
        cur = f"ov{n}"

    # captions LAST so they sit on top of the b-roll, not under it
    # fontsdir pins the face to the one shipped with the skill, so libass
    # cannot quietly substitute whatever the machine happens to have.
    # Both paths are escaped. The ass file's own path was going in raw, and it
    # is derived from the output path the user gave us - so on Windows it
    # always carried a drive colon and the filter never saw a whole filename.
    fc.append(f"[{cur}]ass={_esc_filter_path(ass)}:"
              f"fontsdir={_esc_filter_path(ASSETS_DIR)}[vout]")
    # loudnorm alone cannot reach -14 on peaky phone audio: its limiter binds
    # around -17. The compressor ahead of it is what makes the target reachable.
    fc.append("[ac]afftdn=nf=-24,"
              "acompressor=threshold=-26dB:ratio=4:attack=5:release=150:makeup=6,"
              f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11,"
              "aresample=48000,aformat=channel_layouts=stereo[aout]")

    run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *ins,
         "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
         # CRF 18 produced 25 Mbps on a source that was 17 - spending half
         # again as many bits as the phone recorded, chasing its own
         # compression artifacts, for a 136MB file on a 45 second video that
         # a student then has to upload from their phone. 22 lands at about
         # 13 Mbps, comfortably under every source in the test set, and at a
         # 2x zoom on a face crop the difference is a slight softening of
         # skin texture. The platforms re-encode it anyway.
         "-c:v", "libx264", "-crf", "22", "-preset", "slow", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
         "-movflags", "+faststart",
         # do not inherit input stream metadata: a phone's rotation flag
         # rode along to the output, so players turned an already-upright
         # frame on its side.
         "-map_metadata", "-1", "-y", out])



# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Auto-edit one talking-head video.")
    ap.add_argument("input")
    ap.add_argument("--out", default=None)
    ap.add_argument("--broll-dir", default=None,
                    help="pre-screened library: <dir>/<kind>/*.mp4")
    ap.add_argument("--no-broll", action="store_true")
    ap.add_argument("--script", default=None,
                    help="the script this video is, e.g. \"Curb Appeal\". "
                         "Identifies it far more reliably than the audio does.")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--plan", default=None,
                    help="render exactly this edit plan (from plan.py, b-roll "
                         "filled in by Claude). Disables all automatic picking.")
    args = ap.parse_args()

    src = str(Path(args.input).expanduser().resolve())
    if not os.path.exists(src):
        sys.exit(f"Not found: {src}")
    out = args.out or str(Path(src).with_name(Path(src).stem + "-edited.mp4"))
    work = Path(out).with_suffix("")
    work = work.with_name(work.name + "-work")
    work.mkdir(parents=True, exist_ok=True)

    for tool in ("ffmpeg",):
        if not shutil.which(tool):
            sys.exit(f"{tool} not installed. Run scripts/setup.sh first.")

    src = normalise_rotation(src, work)
    info = probe(src)
    if not info["has_audio"]:
        sys.exit("That video has no audio track, so there is nothing to cut to.")
    print(f"[1/6] {Path(src).name}  {info['duration']:.1f}s  "
          f"{info['width']}x{info['height']}")

    print("[2/6] transcribing (local, no upload)...")
    tr = transcribe(src, work)
    _rec = recover_truncated_tail(work, tr, info["duration"])
    if _rec:
        print(f"      recovered {_rec} word(s) Whisper dropped off the end")
    try:
        import master_scripts as _ms                           # noqa: PLC0415
        _m0, _, _ = _ms.identify(" ".join(x["text"] for x in tr["segments"]))
        _mt = f"{_m0['hook']} {_m0['body']}" if _m0 else None
    except Exception:
        _mt = None
    _unver = verify_tail(work, tr, _mt)
    if _unver:
        _ids = {id(s) for s in _unver}
        tr["segments"] = [s for s in tr["segments"] if id(s) not in _ids]
        print(f"      dropped {len(_unver)} tail segment(s) a second pass did not hear")
    kept_segs, halluc = strip_hallucination(tr["segments"], master_text=_mt,
                                            workdir=work)
    # ALWAYS take what came back. Guarding this on `halluc` being non-empty
    # silently discarded the trimmed tail, because a tail trimmed by the
    # chatter rule reports nothing as "hallucinated" - so "Thank you." and a
    # trailing "..." survived into two renders after the fix that removes them.
    if halluc:
        print(f"      dropped {len(halluc)} trailing segment(s): "
              f"\"{halluc[0]['text'].strip()[:40]}\"")
    tr["segments"] = kept_segs
    tr["text"] = " ".join(s["text"] for s in kept_segs)
    words = [w for s in tr["segments"] for w in s.get("words", [])]
    if not words:
        sys.exit("No speech found. Is there someone talking in this video?")
    print(f"      {len(words)} words")

    retakes = find_retakes(tr["segments"])
    for r in retakes:
        print(f"[3/6] retake at {r['drop']['s']:.1f}s "
              f"(containment {r['containment']}) -> keeping the later take")
    if not retakes:
        print("[3/6] no retakes found")

    slates = find_slates(tr["segments"])
    for s in slates:
        print(f"[3/6] slate \"{s['slate'][:28]}\" at {s['s']:.1f}s "
              f"- cutting the abandoned take back to {s['s']:.1f}s")
    keeps = build_keep_ranges(words, retakes, info["duration"], slates=slates)
    mapped, total = remap(words, keeps)
    # Whisper mishears words the student read correctly, and a wrong word
    # burned into a caption is the most visible failure there is. Fix it
    # against the script she was reading. Single-word swaps only.
    try:
        import master_scripts as _ms2                           # noqa: PLC0415
        _sc, _, _ = _ms2.identify(" ".join(w["w"] for w in mapped))
        if _sc:
            for _h, _g in _ms2.correct_against_master(mapped, _sc):
                print(f'      heard "{_h}" - the script says "{_g}", using the script')
    except Exception:
        pass
    saved = info["duration"] - total
    print(f"[4/6] {info['duration']:.1f}s -> {total:.1f}s  (cut {saved:.1f}s)")

    cues = build_cues(mapped)
    trf = stabilize_pass1(src, work)
    zooms = [0.94] if len(keeps) == 1 else [0.94] * (len(keeps) - 1) + [0.86]
    forced = os.environ.get("CLEANCUT_CAPTION_MARGIN")
    mv = (int(forced) if forced else
          caption_margin(src, str(Path(__file__).parent / 'yunet.onnx'),
                         keeps, work, zooms, trf))
    acc = accent_color(src, keeps)
    print(f"      captions: margin {mv}px from bottom, accent {acc}")
    ass = work / "captions.ass"
    write_ass(cues, ass, margin_v=mv, accent=acc)

    PLAN = json.loads(Path(args.plan).read_text()) if args.plan else None
    script_name, allowed = detect_script(tr.get("text", ""),
                                         (PLAN or {}).get("script") or args.script)
    if script_name:
        print(f"      script: {script_name}"
              f"{' (from --script)' if args.script else ' (guessed from audio)'}"
              f"  -> {', '.join(allowed)}")

    slots, clips = [], []
    if PLAN is not None:
        # Rendering a plan: take the inserts as written and invent nothing.
        bd = Path(args.broll_dir).expanduser() if args.broll_dir else None
        for b in PLAN.get("broll", []):
            c = bd / b["category"] / b["clip"] if bd else None
            if c and c.exists():
                slots.append({"start": float(b["start"]), "end": float(b["end"]),
                              "kind": b["category"], "over": b.get("why", "")})
                clips.append(str(c))
            else:
                print(f"      plan names a missing clip: {b.get('category')}/{b.get('clip')}")
        print(f"[5/6] b-roll: {len(clips)} insert(s) from the plan"
              + ("" if clips else "  (plan is empty - talking head throughout)"))
    elif not args.no_broll and args.broll_dir:
        bd = Path(args.broll_dir).expanduser()
        slots = pick_slots(mapped, total, allowed=allowed or None)
        used: set = set()
        matched = []
        for s in slots:
            c = library_clip(bd, s["kind"], used)
            if c:
                matched.append(s)
                clips.append(c)
        slots = matched
        # The floor is about finished inserts, not intended ones. If a category
        # ran dry in the library, retry that slot with any category that still
        # has a clip rather than silently shipping fewer than SLOT_MIN.
        if len(slots) < SLOT_MIN:
            for s in [x for x in pick_slots(mapped, total, allowed=allowed or None)
                      if x not in matched]:
                if len(slots) >= SLOT_MIN:
                    break
                for kind in CATEGORY_FALLBACK:
                    c = library_clip(bd, kind, used)
                    if c:
                        s = dict(s); s["kind"] = kind
                        slots.append(s); clips.append(c)
                        break
    if PLAN is not None:
        pass
    elif clips and len(clips) < SLOT_MIN:
        print(f"[5/6] b-roll: {len(clips)} slot(s) - BELOW the {SLOT_MIN} floor. "
              f"Library has no clip left for the remaining slots; add clips to "
              f"{args.broll_dir}")
    else:
        print(f"[5/6] b-roll: {len(clips)} slot(s) filled"
              + ("" if clips else "  (no library given, talking head throughout)"))

    mask = work / "cardmask.png"
    if clips:
        make_card_mask(mask)
    else:
        make_card_mask(mask)           # harmless; render still references it

    print("[6/6] rendering...")
    # headliner-cut rule 4: "repetition creates style, variety is a special
    # effect" - one transition language everywhere, and exactly one different
    # thing at the biggest moment. The biggest moment in these scripts is
    # always the close, so the final segment gets a tighter punch than the
    # body. With no retake to cut there is only one segment, so a lone segment
    # still gets the body framing rather than an unearned push.
    render(src, out, keeps, slots, clips, ass, mask, zooms, trf)

    final = probe(out)
    report = {
        "input": src, "output": out,
        "duration_before": round(info["duration"], 2),
        "duration_after": round(final["duration"], 2),
        "seconds_cut": round(info["duration"] - final["duration"], 2),
        "resolution": f"{final['width']}x{final['height']}",
        "words": len(words), "caption_cues": len(cues),
        "cue_text": [c["t"] for c in cues],
        "cue_eos": [bool(c.get("eos")) for c in cues],
        "cue_forced": [bool(c.get("forced")) for c in cues],
        "cue_span": [[round(c["s"], 2), round(c["e"], 2)] for c in cues],
        "cue_margin": [int(c.get("margin", 0)) for c in cues],
        "caption_line_px": CAPTION_LINE_PX,
        "script": script_name, "allowed_categories": allowed,
        "slot_stats": PICK_STATS,
        "caption_margin_v": mv, "accent": acc,
        "stabilised": bool(trf),
        "retakes": [{"containment": r["containment"],
                     "cut": [round(r["drop"]["s"], 2), round(r["drop"]["e"], 2)],
                     "text": r["drop"]["t"]} for r in retakes],
        "keep_ranges": keeps,
        "broll": [{"at": [s["start"], s["end"]], "kind": s["kind"],
                   "over": s["over"], "clip": os.path.basename(c)}
                  for s, c in zip(slots, clips)],
        "transcript": tr.get("text", "").strip(),
    }
    rp = Path(out).with_suffix(".report.json")
    rp.write_text(json.dumps(report, indent=1))

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\nDone: {out}")
    print(f"Report: {rp}")


if __name__ == "__main__":
    main()
