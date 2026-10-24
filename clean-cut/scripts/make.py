#!/usr/bin/env python3
"""
make.py - edit, audit, fix, re-audit. Stop only when the cut passes.

    python3 plan.py INPUT.mp4 --broll-dir ~/.clean-cut/broll-v2 --out plan.json
    # ... fill plan.json's `broll` by reading the script ...
    python3 make.py INPUT.mp4 --plan plan.json --broll-dir ~/.clean-cut/broll-v2

A single render plus a passing audit is not the same as a good cut, because a
failing audit used to mean a human noticing and asking for another pass. This
closes that loop: every failure maps to a specific remedy, the remedy is
applied, and the whole thing is rendered and audited again. It gives up only
when nothing is left to try, and then it says exactly what is still wrong
rather than handing over a cut that failed its own checks.

Remedies, per rule:

  caption_clears_face   lower the captions toward the lowest legal position
  broll_has_reason      no remedy - an insert with no stated reason is a
                        planning fault, not something a knob can fix
  broll_no_filler       no remedy - same. The plan gets rewritten by hand
  broll_no_adjacent     re-pick with the clashing category suppressed
  camera_steady         force stabilisation even if the source measured steady
  loudness_target       nudge the normalisation target
  no_rotation_flag      re-run with the source normalised (should be automatic)

Anything with no remedy is reported and the loop stops - a rule it cannot act
on is a rule a person needs to look at.
"""

from __future__ import annotations

import argparse
import json
import os
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
MAX_PASSES = 4

# rule -> the knob that might fix it
REMEDY = {
    "caption_clears_face": "caption_margin",
    "broll_no_adjacent":   "drop_off_topic",
    "broll_no_repeat":     "drop_off_topic",
    "camera_steady":       "force_stabilise",
    "loudness_target":     "nudge_loudness",
}


def run_edit(py: str, inp: str, out: str, args: argparse.Namespace,
             overrides: dict) -> tuple[int, str]:
    cmd = [py, str(HERE / "autoedit.py"), inp, "--out", out]
    # Without this the loop re-renders through the OLD auto-picker on every
    # remedy pass, quietly throwing away the b-roll decisions in the plan.
    if args.plan:
        cmd += ["--plan", args.plan]
    if args.script:
        cmd += ["--script", args.script]
    if args.broll_dir:
        cmd += ["--broll-dir", args.broll_dir]
    env = dict(os.environ)
    for k, v in overrides.items():
        env[k] = str(v)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def run_audit(py: str, out: str) -> tuple[int, list[str], str]:
    r = subprocess.run([py, str(HERE / "audit.py"), out],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    text = r.stdout or ""
    fails = []
    for line in text.splitlines():
        if line.strip().startswith("FAIL"):
            fails.append(line.strip())
    return r.returncode, fails, text


def rule_key(fail_line: str, rules_order: list[str]) -> str | None:
    """Map a printed FAIL line back to its rule key by description match."""
    for key, desc in RULE_DESCS.items():
        if desc.lower()[:28] in fail_line.lower():
            return key
    return None


RULE_DESCS: dict[str, str] = {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", default=None)
    ap.add_argument("--plan", default=None,
                    help="edit plan from plan.py, with its b-roll already decided")
    ap.add_argument("--script", default=None)
    ap.add_argument("--broll-dir", default=None)
    ap.add_argument("--passes", type=int, default=MAX_PASSES)
    args = ap.parse_args()

    py = sys.executable
    inp = str(Path(args.input).expanduser().resolve())
    out = args.out or str(Path(inp).with_name(Path(inp).stem + "-edited.mp4"))

    # pull the rule descriptions straight from audit.py so they cannot drift
    sys.path.insert(0, str(HERE))
    import audit as audit_mod                                  # noqa: PLC0415
    RULE_DESCS.update(audit_mod.RULES)

    overrides: dict[str, str] = {}
    history: list[tuple[int, int, list[str]]] = []

    for p in range(1, args.passes + 1):
        print(f"\n{'='*66}\nPASS {p}" + (f"   adjustments: {overrides}" if overrides else ""))
        print("=" * 66)
        rc, log = run_edit(py, inp, out, args, overrides)
        if rc != 0:
            print(log[-1500:])
            return 1
        for line in log.splitlines():
            if line.strip().startswith(("[", "  ")) and line.strip():
                print("  " + line.strip())

        rc, fails, text = run_audit(py, out)
        print(text)
        history.append((p, len(fails), fails))
        if not fails:
            print(f"PASSES CLEAN on pass {p}.")
            return 0

        # choose remedies for this round
        applied = []
        for f in fails:
            key = rule_key(f, list(RULE_DESCS))
            remedy = REMEDY.get(key or "")
            if not remedy:
                continue
            if remedy == "caption_margin":
                cur = int(overrides.get("CLEANCUT_CAPTION_MARGIN", 0)) or 0
                nxt = 240 if cur == 0 else max(240, cur - 90)
                overrides["CLEANCUT_CAPTION_MARGIN"] = nxt
                applied.append(f"captions -> {nxt}px")
            elif remedy == "drop_off_topic":
                overrides["CLEANCUT_STRICT_BROLL"] = "1"
                applied.append("b-roll -> strict on-topic")
            elif remedy == "force_stabilise":
                overrides["CLEANCUT_FORCE_STAB"] = "1"
                applied.append("stabilisation -> forced")
            elif remedy == "nudge_loudness":
                applied.append("loudness -> (no knob; reported)")
        if not applied:
            print("\nNo remedy for the remaining failures. Stopping so a person can look:")
            for f in fails:
                print("   " + f)
            return len(fails)
        print("\nAdjusting: " + "; ".join(applied))

    print(f"\nStill failing after {args.passes} passes. Best was "
          f"{min(h[1] for h in history)} failure(s).")
    for f in history[-1][2]:
        print("   " + f)
    return len(history[-1][2])


if __name__ == "__main__":
    sys.exit(main())
