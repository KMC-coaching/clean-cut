#!/usr/bin/env python3
"""Edit one video, start to finish. The command a person actually types.

    python3 scripts/run.py "my-video.mp4"
    python3 scripts/run.py "my-video.mp4" --out finished.mp4
    python3 scripts/run.py "my-video.mp4" --no-broll

Plans the edit, renders it, audits the result, and fixes what the audit can
fix - then says where the file is and what was changed.

It re-executes itself inside the private environment setup.py built, so it
can be started with whatever `python3` the machine happens to have. That is
the point: the documented command used to be
`~/.clean-cut/venv/bin/python ...`, which is simply not the path on Windows,
so the one command in the instructions was wrong for every Windows user.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cc_paths as P                                           # noqa: E402


def reexec_in_venv() -> None:
    """Hand over to the venv interpreter, once, if we are not already it."""
    if P.in_our_venv() or os.environ.get("CLEAN_CUT_NO_REEXEC"):
        return
    py = P.venv_python()
    if not py.exists():
        sys.exit("clean-cut is not set up on this machine yet.\n"
                 "  Run:  python3 " + str(HERE / "setup.py"))
    env = dict(os.environ, CLEAN_CUT_NO_REEXEC="1")
    sys.exit(subprocess.run([str(py), str(Path(__file__).resolve()), *sys.argv[1:]],
                            env=env).returncode)


reexec_in_venv()
P.use_private_tools()


def main() -> int:
    ap = argparse.ArgumentParser(description="Edit a talking-head video.")
    ap.add_argument("video", help="the file to edit")
    ap.add_argument("--out", help="where to write the finished cut")
    ap.add_argument("--no-broll", action="store_true",
                    help="talking head only, no inserts")
    ap.add_argument("--keep-plan", action="store_true",
                    help="leave the plan file next to the output for inspection")
    args = ap.parse_args()

    src = Path(args.video).expanduser().resolve()
    if not src.is_file():
        sys.exit(f"No such video: {src}")

    out = (Path(args.out).expanduser().resolve() if args.out
           else src.with_name(src.stem + "-edited.mp4"))
    out.parent.mkdir(parents=True, exist_ok=True)
    plan = out.with_suffix(".plan.json")

    broll: list[str] = []
    if not args.no_broll and P.BROLL_DIR.is_dir():
        broll = ["--broll-dir", str(P.BROLL_DIR)]
    elif not args.no_broll:
        print(f"note: no b-roll library at {P.BROLL_DIR}, "
              "rendering a talking-head cut", flush=True)

    print(f"\n>> planning   {src.name}", flush=True)
    r = subprocess.run([sys.executable, str(HERE / "plan.py"), str(src),
                        *broll, "--out", str(plan)])
    if r.returncode != 0:
        return r.returncode

    print(f"\n>> editing    -> {out.name}", flush=True)
    # make.py renders, audits, applies what remedies exist, and re-audits.
    r = subprocess.run([sys.executable, str(HERE / "make.py"), str(src),
                        "--plan", str(plan), *broll, "--out", str(out)])

    if not args.keep_plan:
        plan.unlink(missing_ok=True)
        # plan.py leaves its scratch beside the plan. Without this a student
        # gets a stray "...plan-work" folder next to their video and has to
        # wonder whether it is safe to delete.
        shutil.rmtree(plan.with_name(plan.stem + "-work"), ignore_errors=True)

    if r.returncode == 0 and out.exists():
        mb = out.stat().st_size / 1048576
        print(f"\n>> finished   {out}  ({mb:.0f} MB)", flush=True)
    else:
        print("\n>> the cut did not pass its own checks - see the audit above",
              flush=True)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
