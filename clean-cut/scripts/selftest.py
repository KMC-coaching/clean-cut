#!/usr/bin/env python3
"""Prove the thing still runs, on whatever machine this is.

    python3 scripts/selftest.py                 # static + path + windows checks
    python3 scripts/selftest.py <any video>     # the above, plus a real 14s edit

Runs anywhere Python does, which is the point: the previous version of this
was a zsh script, so the one tool for "did I break it" did not exist on
Windows.

It exists because a refactor once deleted two functions, the file still
parsed, and a ten-video batch ran for ten minutes before dying on NameError.
Parsing is not evidence that it runs.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cc_paths as P                                           # noqa: E402

PY = str(P.venv_python()) if P.venv_python().exists() else sys.executable
fails: list[str] = []


def head(msg: str) -> None:
    print(f"\n-- {msg}", flush=True)


def bad(msg: str) -> None:
    fails.append(msg)
    print(f"   FAIL {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"   ok   {msg}", flush=True)


# ------------------------------------------------------------------ static

def check_parses() -> None:
    head("every script parses")
    for f in sorted(HERE.glob("*.py")):
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad(f"{f.name}: {e}")
            return
    ok(f"{len(list(HERE.glob('*.py')))} files")


def check_lint() -> None:
    head("pyflakes")
    # A linter that is not installed reports nothing, which reads exactly like
    # a pass. Say which it was.
    if subprocess.run([PY, "-c", "import pyflakes"],
                      capture_output=True).returncode != 0:
        bad("pyflakes is not installed in this environment - NOT checked")
        return
    r = subprocess.run([PY, "-m", "pyflakes", *[str(p) for p in HERE.glob("*.py")]],
                       capture_output=True, text=True)
    lines = [ln for ln in r.stdout.splitlines()
             if "imported but unused" not in ln and "unable to detect" not in ln]
    for ln in lines:
        print(f"        {ln}")
    ok("no undefined names" if not lines else f"{len(lines)} warning(s) above")


def check_names_resolve() -> None:
    """Every name autoedit.py calls is defined somewhere it can see."""
    head("every name in autoedit.py resolves")
    tree = ast.parse((HERE / "autoedit.py").read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

    def names(node):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, (ast.Tuple, ast.List)):
            for e in node.elts:
                yield from names(e)

    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                defined |= set(names(t))
        elif isinstance(n, (ast.For, ast.comprehension)):
            defined |= set(names(n.target))
        elif isinstance(n, ast.arg):
            defined.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            defined |= {(a.asname or a.name).split(".")[0] for a in n.names}
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    missing = sorted(called - defined - set(dir(__import__("builtins"))))
    if missing:
        bad(f"undefined calls: {', '.join(missing)}")
    else:
        ok("none undefined")


# ------------------------------------------------------------------ windows

def check_windows_shapes() -> None:
    """The Windows-only code paths, checked as shapes on any platform.

    None of this proves Windows. It proves that the parts which differ on
    Windows do the right thing when handed Windows-shaped input, which is
    the most that can be established without the machine.
    """
    head("windows-shaped input")

    from autoedit import _esc_filter_path                      # noqa: PLC0415
    esc = _esc_filter_path(Path(r"C:\Users\Tester\.clean-cut\out-work\captions.ass"))
    if "\\\\:" in esc and "\\" not in esc.replace("\\\\:", ""):
        ok(f"drive colon escaped for the filtergraph: {esc}")
    else:
        bad(f"drive letter not escaped correctly: {esc}")

    # cc_paths puts the interpreter in Scripts/ on Windows and bin/ elsewhere.
    # Getting that wrong made every documented command wrong for Windows.
    real = P.WINDOWS
    try:
        P.WINDOWS, P.EXE = True, ".exe"
        win_py, win_ff = P.venv_python(), P.private_ffmpeg()
        P.WINDOWS, P.EXE = False, ""
        nix_py, nix_ff = P.venv_python(), P.private_ffmpeg()
    finally:
        P.WINDOWS = real
        P.EXE = ".exe" if real else ""
    if win_py.parent.name == "Scripts" and win_py.name == "python.exe":
        ok(f"venv interpreter on windows: ...{os.sep}Scripts{os.sep}python.exe")
    else:
        bad(f"wrong venv interpreter for windows: {win_py}")
    if nix_py.parent.name == "bin" and nix_py.name == "python":
        ok(f"venv interpreter elsewhere:   ...{os.sep}bin{os.sep}python")
    else:
        bad(f"wrong venv interpreter for posix: {nix_py}")
    if win_ff.name == "ffmpeg.exe" and nix_ff.name == "ffmpeg":
        ok("private ffmpeg named correctly on both")
    else:
        bad(f"private ffmpeg naming: {win_ff.name} / {nix_ff.name}")


def check_ffmpeg_builds() -> None:
    head("a static ffmpeg is listed for this machine")
    import platform                                            # noqa: PLC0415

    import setup as S                                          # noqa: PLC0415
    key = (platform.system(), platform.machine())
    if key in S.FFMPEG_BUILDS:
        ok(f"{key[0]} {key[1]} -> {S.FFMPEG_BUILDS[key][0].rsplit('/', 1)[-1]}")
    else:
        bad(f"no build listed for {key} - setup cannot self-heal a bad ffmpeg here")
    for k in sorted(S.FFMPEG_BUILDS):
        if k != key:
            print(f"        also listed: {k[0]} {k[1]}")


# ------------------------------------------------------------------ paths

def check_paths() -> None:
    head("awkward paths survive the filtergraph")
    r = subprocess.run([PY, str(HERE / "test_paths.py")], capture_output=True,
                       text=True)
    for ln in r.stdout.splitlines():
        if any(k in ln for k in ("FAIL", "passed", "skip")):
            print(f"        {ln.strip()}")
    if r.returncode != 0:
        # Print everything when it fails. Filtering to lines containing "FAIL"
        # hid the actual reason on the first Windows run and left "see above"
        # pointing at nothing.
        for ln in (r.stdout + r.stderr).splitlines():
            print(f"        {ln}")
        bad("test_paths.py failed - output above")
    else:
        ok("every case rendered")


# ------------------------------------------------------------------ pipeline

def check_pipeline(video: str) -> None:
    head("a real 14 second edit, end to end")
    with tempfile.TemporaryDirectory() as td:
        w = Path(td)
        slice_ = w / "in.mp4"
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "6",
                            "-t", "14", "-i", video, "-c", "copy", "-y",
                            str(slice_)], capture_output=True, text=True)
        if r.returncode != 0 or not slice_.exists():
            bad(f"could not cut a slice from {video}: {r.stderr.strip()[:120]}")
            return
        out = w / "out.mp4"
        env = dict(os.environ, CLEAN_CUT_NO_REEXEC="1")
        r = subprocess.run([PY, str(HERE / "run.py"), str(slice_),
                            "--out", str(out)], env=env,
                           capture_output=True, text=True)
        tail = r.stdout.splitlines()
        # A 14s slice CANNOT meet the runtime floor; that one failure is by
        # construction and is not a regression.
        real_fails = [ln for ln in tail
                      if ln.strip().startswith("FAIL")
                      and "finished runtime" not in ln]
        for ln in real_fails:
            print(f"        {ln.strip()}")
        if real_fails:
            bad(f"{len(real_fails)} audit rule(s) failed for a reason other "
                "than the runtime floor")
        elif not out.exists():
            print("\n".join(tail[-12:]))
            bad("no output file was produced")
        else:
            mb = out.stat().st_size / 1048576
            ok(f"rendered and audited ({mb:.0f} MB)")


def main() -> int:
    print("clean-cut selftest")
    print(f"   {sys.platform}, python {sys.version.split()[0]}")
    print(f"   using {PY}")

    check_parses()
    check_lint()
    check_names_resolve()
    check_windows_shapes()
    check_ffmpeg_builds()
    check_paths()
    if len(sys.argv) > 1:
        check_pipeline(sys.argv[1])
    else:
        head("a real edit")
        print("   skipped - pass a video path to include it")

    print("\n" + "-" * 66)
    if fails:
        print(f"{len(fails)} problem(s):")
        for f in fails:
            print(f"   {f}")
        return 1
    print("selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
