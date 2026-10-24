#!/usr/bin/env python3
"""
audit.py - check a finished cut against the rules. Pass or fail, no opinions.

    python3 audit.py FINISHED.mp4

Reads the report JSON written next to the video and inspects the pixels, then
prints one line per rule. Exit code is the number of FAILs, so it can gate a
batch.

Every rule here exists because a render broke it. The skill kept shipping
videos whose faults were only visible by watching them one at a time - a
hallucinated tail, an insert that contradicted the line, one lonely b-roll
clip. headliner-cut's own audit pass caught 19 problems "invisible while
watching at speed", and this is the same idea pointed at this pipeline.
"""

from __future__ import annotations

import json
import re
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

# ---- THE RULES. A finished cut must satisfy every one. ----
RULES = {
    "vertical_1080x1920":   "frame is exactly 1080x1920"
    , "no_rotation_flag":    "no stale rotation flag on the output",
    "loudness_target":      "integrated loudness within 1.5 LUFS of -14",
    "broll_no_filler":      "no insert placed just to hit a count",
    "broll_no_adjacent":    "no two neighbouring inserts share a category",
    "broll_no_repeat":      "no clip used twice in one video"
    , "broll_has_reason":    "every insert states why it belongs"
    , "captions_read_as_phrases": "no caption is one word or ends mid-phrase"
    , "captions_fit_the_frame": "no caption is too wide to fit on screen",
    "hook_clear":           "no insert in the first 3 seconds",
    "caption_clears_face":  "captions clear eyes+mouth in typical framing"
    , "caption_clears_ui":    "captions stay out of the bottom 220px UI strip",
    "no_black_frames":      "no near-black frames",
    "no_frozen_frames":     "no still run longer than 0.4s",
    "no_hallucinated_tail": "transcript does not end in a repeated phrase",
    "camera_steady":        "residual shake under 0.6 px/frame (speaker)",
    "duration_sane":        "finished runtime between 15s and 180s",
}


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: audit.py FINISHED.mp4")
    vid = Path(sys.argv[1]).expanduser().resolve()
    rep = vid.with_suffix(".report.json")
    if not rep.exists():
        sys.exit(f"No report beside the video: {rep}")
    r = json.loads(rep.read_text())

    import cv2                                                 # noqa: PLC0415
    import numpy as np                                         # noqa: PLC0415
    import subprocess                                          # noqa: PLC0415

    results: dict[str, tuple[bool, str]] = {}
    cap = cv2.VideoCapture(str(vid))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = n / fps if fps else 0.0

    # cv2 honours the display matrix, so w/h here is what a PLAYER sees - which
    # is the point. A stabilised phone clip shipped stored-portrait but flagged
    # -90, so every player turned it on its side.
    results["vertical_1080x1920"] = (w == 1080 and h == 1920, f"{w}x{h} as played")
    rot_err = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(vid)],
                             capture_output=True, text=True).stderr
    has_rot = "displaymatrix" in rot_err.lower()
    results["no_rotation_flag"] = (not has_rot,
                                   "present - will play sideways" if has_rot else "none")
    results["duration_sane"] = (15 <= dur <= 180, f"{dur:.1f}s")

    # ---- audio ----
    err = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(vid),
                          "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.findall(r"I:\s*(-?[\d.]+) LUFS", err)
    lufs = float(m[-1]) if m else None
    results["loudness_target"] = (lufs is not None and abs(lufs + 14.0) <= 1.5,
                                  f"{lufs} LUFS" if lufs else "unmeasured")

    # ---- b-roll ----
    br = r.get("broll", [])
    kinds = [b["kind"] for b in br]
    clips = [b.get("clip", "") for b in br]
    # The floor is GONE. It required three inserts, and when the cues produced
    # one it placed two by position - stamping them "(placed to meet the
    # 3-insert floor)" in the report. Four of six inserts across two videos
    # were decoration. Two that belong beat three where one is padding.
    filler = [b for b in br if "floor" in (b.get("over") or "").lower()
              or not (b.get("over") or "").strip()]
    results["broll_no_filler"] = (not filler,
                                  f"{len(filler)} filler insert(s)" if filler
                                  else f"{len(br)} insert(s), none placed to hit a count")
    results["broll_no_adjacent"] = (
        all(kinds[i] != kinds[i + 1] for i in range(len(kinds) - 1)),
        " -> ".join(kinds) or "none")
    results["broll_no_repeat"] = (len(set(clips)) == len(clips),
                                  f"{len(set(clips))} distinct of {len(clips)}")
    # Relevance is no longer gated by a category allow-list. That list came
    # from watching ONE student's delivered videos, and it rejected a correct
    # placement while passing a toolbox in a mortgage video. What is checkable
    # is that a human-or-agent decision was made and written down: each insert
    # carries the reason it belongs. A reason can be judged; a bucket cannot.
    noreason = [b.get("kind") for b in br
                if len((b.get("over") or "").strip()) < 20]
    results["broll_has_reason"] = (not noreason,
                                   f"no stated reason: {', '.join(map(str, noreason))}"
                                   if noreason else f"all {len(br)} carry a reason")
    # Found by watching, not by auditing: "situations" alone on screen, and
    # "and protecting your" with its noun on the next card. The chunker took
    # three words at a time and broke wherever the phrase happened to fall.
    # This is the check that keeps the fix from quietly coming undone.
    DANGLING = {
        "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at",
        "for", "with", "from", "by", "into", "than", "then", "as", "if", "so",
        "because", "while", "when", "that", "this", "these", "those", "who",
        "which", "what", "your", "my", "our", "their", "his", "her", "its",
        "is", "are", "was", "were", "be", "been", "am", "do", "does", "did",
        "has", "have", "had", "will", "would", "can", "could", "we",
        "they", "he", "she", "i", "through", "about", "after", "before",
        "over", "under", "between", "during", "without", "within", "around",
        "across", "toward", "towards",
    }
    # "Don't let this be you." is a whole sentence that ends on a pronoun, and
    # the first version of this rule failed seven of ten videos for it. A
    # pronoun only dangles MID-sentence, so it is judged against where the
    # sentence actually ends rather than banned outright.
    OBJECT_PRONOUNS = {"you", "it", "them", "us", "me", "him", "her",
                       "that", "this"}
    cue_text = r.get("cue_text") or []
    cue_eos = r.get("cue_eos") or [False] * len(cue_text)
    # A split the splitter had no clean alternative to is not a defect. Some
    # sentences cannot be divided both narrow enough and cleanly - failing
    # those is the "unsatisfiable rule" RULES.md warns against.
    cue_forced = r.get("cue_forced") or [False] * len(cue_text)
    orphan = [c for c, f in zip(cue_text, cue_forced)
              if len(c.split()) == 1 and not f]
    dangle = []
    for c, eos, forced in zip(cue_text, cue_eos, cue_forced):
        if not c.split() or forced:
            continue
        last = re.sub(r"[^a-z']", "", c.split()[-1].lower())
        # Pronouns FIRST. "this" and "that" are in both sets - determiners in
        # "that home", sentence enders in "buyers approach this." - so testing
        # DANGLING first failed a correct caption.
        if last in OBJECT_PRONOUNS:
            if not eos:
                dangle.append(c)
        elif last in DANGLING:
            dangle.append(c)
    bad_caps = orphan + dangle
    results["captions_read_as_phrases"] = (
        not bad_caps,
        (f"{len(orphan)} one-word, {len(dangle)} dangling: "
         + "; ".join(f'"{c}"' for c in bad_caps[:4]))
        if bad_caps else
        (f"all {len(cue_text)} read as phrases" if cue_text else "no cue text in report"))

    # Width, not character count. "The seller's agent only" and "crushed in
    # negotiations" are both 23 characters; the first fits on one line and the
    # second wraps, because the glyphs differ. Measured against what libass
    # actually did at Helvetica Neue Bold 86px: 943px stayed on one line, 962
    # wrapped. A wrapped caption pushes its first line UP into the face - on
    # 04 it put "crushed in" straight across the speaker's mouth.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # Import the limit as well as the measurement, so the renderer and the
    # check can never disagree about where the frame ends.
    try:
        from autoedit import HARD_CAPTION_PX, text_width       # noqa: PLC0415
    except Exception:
        HARD_CAPTION_PX = 1270
        def text_width(x):                                     # noqa: ANN001
            return len(x) * 38.0
    wide = [c for c in cue_text if text_width(c) > HARD_CAPTION_PX]
    results["captions_fit_the_frame"] = (
        not wide,
        (f"{len(wide)} would wrap: " + "; ".join(f'"{c}"' for c in wide[:3]))
        if wide else
        f"widest is {max((text_width(c) for c in cue_text), default=0):.0f}px (max {HARD_CAPTION_PX})")

    results["hook_clear"] = (all(b["at"][0] >= 3.0 for b in br),
                             f"first insert at {br[0]['at'][0]}s" if br else "n/a")

    # ---- transcript tail ----
    txt = r.get("transcript", "")
    sents = [s for s in re.split(r"(?<=[.?!])\s+", txt) if len(s.split()) >= 4]
    tail_dupe = False
    if len(sents) >= 3:
        last3 = [" ".join(re.findall(r"[a-z0-9']+", s.lower())) for s in sents[-3:]]
        tail_dupe = last3[0] == last3[1] or last3[1] == last3[2]
    results["no_hallucinated_tail"] = (not tail_dupe, txt[-48:].strip() or "empty")

    # ---- pixels: black, frozen, caption-over-face ----
    model = HERE / "yunet.onnx"
    det = (cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.6, 0.3, 5000)
           if model.exists() else None)
    # read the margin the render actually used, never assume one
    margin_v = int(r.get("caption_margin_v", 470))
    # Captions carry their OWN margin now. Checking every frame against one
    # style default is how 05 and 09 passed while sitting on a chin, and how
    # 09 then failed while sitting on a collar - the line being measured was
    # not the line the caption was on.
    spans = r.get("cue_span") or []
    cmargins = r.get("cue_margin") or []
    line_px = int(r.get("caption_line_px", 95))

    def margin_at(t_s: float) -> int:
        for (a, b), m in zip(spans, cmargins):
            if a - 0.05 <= t_s <= b + 0.05 and m:
                return int(m)
        return margin_v
    probes_done = 0
    black = over_face = 0
    step = max(1, n // 100)
    for i in range(0, n, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(cv2.resize(fr, (160, 284)), cv2.COLOR_BGR2GRAY)
        if g.mean() < 12:
            black += 1
        if det is not None:
            probes_done += 1
            det.setInputSize((fr.shape[1], fr.shape[0]))
            _, faces = det.detect(fr)
            # The caption active at THIS moment, not the style default.
            cap_top = fr.shape[0] - margin_at(i / (fps or 30.0)) - line_px
            # Score the SPEAKER: the largest detection in frame. Largest-box
            # selection drops the small spurious hits on hair and collars on
            # its own, without a confidence or size gate.
            #
            # An earlier version added gates of 0.80 confidence and 14% height
            # and took the overlap count from 40 to 1. That was not a fix, it
            # was tuning the check until the failure disappeared - the captions
            # really were across the speaker's mouth, and Jerry saw it in the
            # first video he played. Never widen a rule to pass a video; fix
            # the video.
            _f = faces if faces is not None else []
            for f in ([max(_f, key=lambda x: x[2] * x[3])] if len(_f) else []):
                # "A caption resting UNDER the chin is normal in short-form"
                # was the intent, and the rule was checking the mouth line -
                # 78% of the box - which let the caption sit ON the chin
                # instead of under it. The bottom 22% is jaw, and on these
                # framings that is 85-155px of face the caption was crossing.
                #
                # Jerry spotted it on a cut that had scored 17/17. Measuring
                # three speakers afterwards, EVERY one overlapped, including
                # the two that passed. The rule now checks what it always
                # meant: the caption must start below the chin.
                # 0.85 of the box, not 1.0. YuNet's box runs past the
                # visible chin onto the neck - measured at ~130px on a 600px
                # box, about 22%. Checking the box bottom flags captions
                # sitting plainly on a collar: 02's worst frame has "Powered
                # by Keller Williams" on his chest and was counted as an
                # overlap. 0.85 still catches the real thing - 05's original
                # caption, which was visibly across the chin, fails at this
                # line by a pixel.
                #
                # Placement still aims at the full box bottom. Being
                # conservative about where to PUT the caption and accurate
                # about where the face ENDS are different jobs.
                chin = f[1] + f[3] * 0.85
                if chin > cap_top:
                    over_face += 1
    cap.release()
    results["no_black_frames"] = (black == 0, f"{black} frame(s)")
    # A real freeze is a SUSTAINED run of identical frames. Comparing frames a
    # third of a second apart in a slow clip flagged two "frozen frames" that
    # were nothing of the kind - stock footage is often lower-fps conformed to
    # 30, so scattered duplicate frames are normal and invisible. Walk
    # consecutive frames and look for a run instead.
    # Scoped to the talking head. Stock b-roll is routinely slow or shot at a
    # lower frame rate and conformed, so half a second of near-stillness inside
    # an insert is normal footage, not a defect - and the slow push already
    # keeps it moving. A freeze on the SPEAKER is the thing that looks broken.
    br_windows = [(b["at"][0], b["at"][1]) for b in br]
    cap2 = cv2.VideoCapture(str(vid))
    run = longest = 0
    prev2 = None
    fi = -1
    while True:
        ok, fr = cap2.read()
        if not ok:
            break
        fi += 1
        if any(x - 0.4 <= fi / (fps or 30) <= y + 0.4 for x, y in br_windows):
            run = 0
            prev2 = None
            continue
        g2 = cv2.cvtColor(cv2.resize(fr, (160, 284)), cv2.COLOR_BGR2GRAY).astype(int)
        if prev2 is not None:
            if float(np.abs(g2 - prev2).mean()) < 0.25:
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        prev2 = g2
    cap2.release()
    frozen_s = longest / (fps or 30)
    results["no_frozen_frames"] = (frozen_s < 0.4,
                                   f"longest still run {frozen_s:.2f}s ({longest} frames)")
    # A speaker who leans towards the lens can drop their face 300px for half a
    # second, and there is nowhere legal to put a caption in that frame. Demand
    # that it is rare, not that it never happens - an unsatisfiable rule is not
    # a rule, it is noise that trains you to ignore the audit.
    # 10%. A speaker who leans right into the lens for a moment is genuinely
    # unfixable, but 25% was chosen to make a failing video pass and that is
    # not what a tolerance is for.
    tol = max(1, int(probes_done * 0.10))
    results["caption_clears_ui"] = (margin_v >= 240,
                                    f"margin {margin_v}px (needs >=240)")
    results["caption_clears_face"] = (
        over_face <= tol,
        f"{over_face}/{probes_done} probe(s) overlap (tolerance {tol}, margin {margin_v}px)")

    # ---- residual shake ----
    # Measured on the TALKING HEAD only. B-roll inserts carry a deliberate slow
    # push, and counting that as camera shake made a tripod-steady source
    # (0.21 px/frame) read as 1.03 in the render - the pipeline failing a rule
    # because of motion it had added on purpose.
    cap = cv2.VideoCapture(str(vid))
    prev, d = None, []
    fi2 = -1
    while len(d) < 300:
        ok, fr = cap.read()
        if not ok:
            break
        fi2 += 1
        if any(x - 0.4 <= fi2 / (fps or 30) <= y + 0.4 for x, y in br_windows):
            prev = None
            continue
        g = cv2.cvtColor(cv2.resize(fr, (320, 568)), cv2.COLOR_BGR2GRAY).astype("float32")
        if prev is not None:
            (dx, dy), _ = cv2.phaseCorrelate(prev, g)
            d.append((dx * dx + dy * dy) ** 0.5)
        prev = g
    cap.release()
    shake = float(np.mean(d)) if d else 0.0
    results["camera_steady"] = (shake < 0.6, f"{shake:.2f} px/frame")

    # ---- report ----
    fails = 0
    print(f"\nAUDIT  {vid.name}\n" + "-" * 72)
    for key, desc in RULES.items():
        ok, detail = results.get(key, (False, "not checked"))
        if not ok:
            fails += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {desc:<46} {detail}")
    print("-" * 72)
    print(f"  {len(RULES) - fails}/{len(RULES)} rules passed\n")
    return fails


if __name__ == "__main__":
    sys.exit(main())
