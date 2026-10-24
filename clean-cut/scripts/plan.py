#!/usr/bin/env python3
"""
plan.py - do the mechanical work, then hand the editorial decisions to Claude.

    python3 plan.py INPUT.mp4 --broll-dir ~/.clean-cut/broll-v2 --out plan.json

Writes an edit plan: the transcript in timed lines, the cuts already decided,
the b-roll library listed, and `broll: []` left EMPTY for Claude to fill.

Why the split. Placement used to be regex over the transcript - first matching
word wins, map it to a category. On a 72-second video about representation and
negotiation that found the word "homes" twice and nothing else, and the floor
padded the rest with clips chosen by position. Four of six inserts across two
videos were decoration.

The scripts are about people and process: buyers, agent, offer, deposit,
mistakes, guidance, strategies. Matching strings cannot tell a subject from a
passing mention - which is how a toolbox ended up in a mortgage video because
the speaker said "inspections" once.

So the machine does what machines are good at (timing, levels, shake, cuts) and
the agent that can actually read the script decides what it means. If nothing
earns an insert, the list stays empty and the cut is a talking head. Nothing
here can invent one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import autoedit as ae                                          # noqa: E402
import master_scripts as ms                                    # noqa: E402


def _join(ws: list[dict]) -> str:
    """Join words, closing up the hyphens Whisper splits into two tokens."""
    import re
    return re.sub(r"\s+-(\w)", r"-\1", " ".join(x["w"] for x in ws))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", default=None)
    ap.add_argument("--broll-dir", default=None)
    ap.add_argument("--script", default=None)
    args = ap.parse_args()

    src = str(Path(args.input).expanduser().resolve())
    out = Path(args.out or Path(src).with_suffix(".plan.json"))
    work = out.with_name(out.stem + "-work")
    work.mkdir(parents=True, exist_ok=True)

    src = ae.normalise_rotation(src, work)
    info = ae.probe(src)
    if not info["has_audio"]:
        sys.exit("That video has no audio track.")

    print(f"[1/4] {Path(args.input).name}  {info['duration']:.1f}s")
    print("[2/4] transcribing...")
    tr = ae.transcribe(src, work)
    # Identify from the RAW transcript, before anything is trimmed, so the
    # trim can be told what the script actually says. Without this the tail
    # rules have no way to tell a mid-sentence Whisper split from a student
    # chatting after the take.
    _rec = ae.recover_truncated_tail(work, tr, info["duration"])
    if _rec:
        print(f"      recovered {_rec} word(s) Whisper dropped off the end")
    raw_text = " ".join(s["text"] for s in tr["segments"])
    m0, _s0, _r0 = ms.identify(raw_text)
    master_text = f"{m0['hook']} {m0['body']}" if m0 else None
    unverified = ae.verify_tail(work, tr, master_text)
    if unverified:
        ids = {id(s) for s in unverified}
        tr["segments"] = [s for s in tr["segments"] if id(s) not in ids]
        print(f"      dropped {len(unverified)} tail segment(s) a second pass "
              f"did not hear: \"{unverified[0]['text'].strip()[:40]}\"")
    kept, halluc = ae.strip_hallucination(tr["segments"], master_text=master_text,
                                          workdir=work)
    if halluc:
        print(f"      dropped {len(halluc)} trailing segment(s): "
              f"\"{halluc[0]['text'].strip()[:40]}\"")
    tr["segments"] = kept
    words = [w for s in tr["segments"] for w in s.get("words", [])]
    if not words:
        sys.exit("No speech found.")

    retakes = ae.find_retakes(tr["segments"])
    for r in retakes:
        print(f"[3/4] retake at {r['drop']['s']:.1f}s - keeping the later take")
    slates = ae.find_slates(tr["segments"])
    for s in slates:
        print(f"      slate \"{s['slate'][:24]}\" at {s['s']:.1f}s - cutting the abandoned take")
    keeps = ae.build_keep_ranges(words, retakes, info["duration"], slates=slates)
    mapped, total = ae.remap(words, keeps)
    # She is reading a known script, so a single word that disagrees with it
    # between two words that agree is a mishearing, not an ad-lib.
    if m0:
        for heard, got in ms.correct_against_master(mapped, m0):
            print(f'      heard "{heard}" - the script says "{got}", using the script')
    print(f"      {info['duration']:.1f}s -> {total:.1f}s")

    shake = ae.measure_shake(src) or {}
    stabilise = bool(shake and (shake.get("mean_px", 0) >= ae.SHAKE_MEAN_TRIGGER
                                or shake.get("p95_px", 0) >= ae.SHAKE_P95_TRIGGER))

    # the transcript as timed lines, on the CUT timeline, for Claude to read
    lines, cur = [], []
    for w in mapped:
        cur.append(w)
        if w["w"].endswith((".", "?", "!")) or len(cur) >= 14:
            lines.append({"start": round(cur[0]["s"], 2), "end": round(cur[-1]["e"], 2),
                          "text": _join(cur)})
            cur = []
    if cur:
        lines.append({"start": round(cur[0]["s"], 2), "end": round(cur[-1]["e"], 2),
                      "text": _join(cur)})

    # Which of the twelve is this? Decided from the words, not the title.
    # Video 04 is filed as "Curb Appeal" and is script 3, "Thinking About
    # Buying" - a title-driven b-roll choice gave it paint and flowerbeds.
    heard = " ".join(l["text"] for l in lines)
    master, score, ranked = ms.identify(heard)
    if master:
        print(f"      script: {master['title']} (matched the master text, "
              f"{score:.0%}; next best {ranked[1][1]:.0%})")
        if args.script and args.script.lower() not in master["title"].lower():
            print(f"      NOTE: filed as \"{args.script}\" - the audio says otherwise. "
                  f"Going with the audio.")
    else:
        print(f"      script: UNIDENTIFIED (best {score:.0%}). Not one of the twelve "
              f"standard scripts - b-roll gets decided from the transcript alone.")

    library = {}
    if args.broll_dir:
        bd = Path(args.broll_dir).expanduser()
        for d in sorted(p for p in bd.iterdir() if p.is_dir()):
            clips = sorted(c.name for c in d.glob("*.mp4"))
            if clips:
                library[d.name] = clips

    intro_at = next((w["s"] for w in mapped if ae.INTRO_RE.search(w["w"])), None)
    dnc = {
        # The masters mark the hook explicitly and the real ones run 4-7s.
        # The old flat [0, 3] let an insert land on the back half of the
        # line that is supposed to stop the scroll.
        "hook": (ms.hook_window(master, mapped) if master else None) or [0.0, 3.0],
        "name_reveal": ([round(intro_at - 0.5, 2), round(intro_at + 4.0, 2)]
                        if intro_at is not None else None),
        "last_seconds": [round(max(0.0, total - 2.0), 2), round(total, 2)],
    }

    # B-roll was decided once, against the master script, with a written
    # reason per insert. Here we only locate those decisions in THIS take.
    broll, notes = (ms.place_bank(master, mapped, total, dnc) if master else ([], []))
    for b in broll:
        print(f"      b-roll {b['start']:.1f}-{b['end']:.1f}s  "
              f"{b['category']}/{b['clip']}")
    for n in notes:
        print(f"      - {n}")
    if master and not broll and not notes:
        print("      no b-roll placed")

    plan = {
        "source": src,
        "script": args.script,
        "duration_cut": round(total, 2),
        "keep_ranges": keeps,
        "stabilise": stabilise,
        "shake": shake,
        "retakes": [{"cut": [round(r["drop"]["s"], 2), round(r["drop"]["e"], 2)],
                     "text": r["drop"]["t"]} for r in retakes],
        "do_not_cover": dnc,
        "script_matched": (master or {}).get("title"),
        "script_match_score": round(score, 3),
        "master_text": (f"{master['hook']} {master['body']}" if master else None),
        "library": library,
        "transcript_lines": lines,
        "broll": broll,
        "_instructions": (
            "Fill `broll` with inserts that genuinely illustrate what is being "
            "said at that moment. Each: {\"start\": s, \"end\": s, \"category\": "
            "\"<library key>\", \"clip\": \"<filename>\", \"why\": \"<one line>\"}. "
            "Leave it EMPTY if nothing earns a place - a talking head beats a "
            "clip that does not belong. Rules: 3.5s each; nothing inside "
            "do_not_cover; at least 5s between inserts; never two of the same "
            "category back to back; never the same clip twice."
        ),
    }
    out.write_text(json.dumps(plan, indent=1))
    print(f"[4/4] plan written: {out}")
    print(f"      hook protected to {plan['do_not_cover']['hook'][1]}s")
    print(f"      {len(lines)} transcript lines, "
          f"{sum(len(v) for v in library.values())} clips across "
          f"{len(library)} categories, {len(broll)} insert(s) placed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
