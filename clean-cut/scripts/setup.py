#!/usr/bin/env python3
"""Put everything clean-cut needs in place, on macOS, Linux or Windows.

    python3 scripts/setup.py          # or setup.sh / setup.ps1, which call this

Safe to run repeatedly; later runs finish in seconds because everything is
cached. Roughly 3 GB on the first run (1.4 GB of Python packages, 1.5 GB of
speech model, 0.4 GB of b-roll).

This is the whole setup. The shell wrappers next to it only find a Python and
hand over, so there is ONE implementation to fix rather than one per platform:
the previous bash-only version simply did not exist for a Windows user.

It never asks for an administrator password and never needs a package
manager. If ffmpeg is missing - or is present but was built without the
optional pieces this needs - it downloads a known-good static build into
~/.clean-cut/bin and uses that one. Those builds were each checked by reading
the configure string out of the binary; all three carry libass, fontconfig,
libvidstab, libx264 and freetype.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cc_paths as P                                           # noqa: E402

# Optional ffmpeg build features the pipeline needs. An ffmpeg without these
# does not refuse the job, it dies partway through a ten-minute render with an
# error naming a filter rather than the cause.
NEED_FILTERS = ("vidstabdetect", "vidstabtransform", "ass", "zoompan",
                "unsharp", "loudnorm", "dynaudnorm", "afftdn", "acompressor",
                "ebur128")
NEED_ENCODERS = ("libx264", "aac")

# Static builds, one per platform. Each was downloaded and its embedded
# configure string read before being listed here.
# Each platform lists SEVERAL urls, tried in order, because the thing that
# actually breaks here is a publisher renaming a file. osxexperts.net ships
# a version number in the filename and deletes the old one when it rotates:
# ffmpeg711intel.zip served fine for months and then became a 404, which
# silently broke every Intel Mac install. A second url means a rotation
# degrades to "the fallback was used" instead of "setup cannot finish".
# The Linux and Windows urls are version-independent by design, so they get
# one entry each; there is nothing to fall back to and nothing to rot.
FFMPEG_BUILDS = {
    ("Darwin", "arm64"):  (("https://www.osxexperts.net/ffmpeg9arm.zip",
                            "https://www.osxexperts.net/ffmpeg711arm.zip"), "zip"),
    ("Darwin", "x86_64"): (("https://www.osxexperts.net/ffmpeg80intel.zip",), "zip"),
    ("Linux", "aarch64"): (("https://johnvansickle.com/ffmpeg/releases/"
                            "ffmpeg-release-arm64-static.tar.xz",), "tar"),
    ("Linux", "x86_64"):  (("https://johnvansickle.com/ffmpeg/releases/"
                            "ffmpeg-release-amd64-static.tar.xz",), "tar"),
    ("Windows", "AMD64"): (("https://www.gyan.dev/ffmpeg/builds/"
                            "ffmpeg-release-essentials.zip",), "zip"),
    ("Windows", "ARM64"): (("https://www.gyan.dev/ffmpeg/builds/"
                            "ffmpeg-release-essentials.zip",), "zip"),
}

# The newest Python is not always the one that works. faster-whisper needs
# onnxruntime, and onnxruntime has never published an Intel-macOS wheel past
# cp313: on an Intel Mac running 3.14 pip cannot resolve it, backtracks through
# old faster-whisper releases, and ends up trying to COMPILE PyAV against
# ffmpeg headers that are not there. The student sees a page of pkg-config
# errors after a two-minute download. So cap the version per platform and
# build the environment with an interpreter that can actually install things.
PY_CEILING = {
    ("Darwin", "x86_64"): ((3, 13), "onnxruntime has no Intel-Mac build above 3.13"),
}


def py_ceiling() -> tuple:
    """(major, minor), reason - or (None, "") when this platform has no cap."""
    hit = PY_CEILING.get((platform.system(), platform.machine()))
    return hit if hit else (None, "")


def _version_of(exe: str) -> tuple:
    try:
        out = subprocess.run(
            [exe, "-c", "import sys;print(*sys.version_info[:3])"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ()
    if out.returncode != 0:
        return ()
    try:
        return tuple(int(x) for x in out.stdout.split())
    except ValueError:
        return ()


def usable_interpreter() -> tuple:
    """The interpreter to build the venv with, and a note about the choice.

    Returns (path, note). path is None when nothing on this machine fits, and
    the note then says what is wrong, for preflight to print.
    """
    cap, why = py_ceiling()
    here = tuple(sys.version_info[:3])
    if cap is None or here[:2] <= cap:
        return sys.executable, ""

    # This interpreter is too new for the platform. Find one that is not.
    # Bare "python3" is in the list on purpose and matters more than the
    # versioned names: on a Mac with the Command Line Tools installed, the
    # only other interpreter is /usr/bin/python3, which is NOT called
    # python3.9 even though that is what it is. Every candidate is asked its
    # version rather than trusted by name.
    names = [f"python3.{m}" for m in range(cap[1], 8, -1)] + ["python3", "python"]
    roots = [None,                                   # whatever is on PATH
             "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin",
             "/Library/Frameworks/Python.framework/Versions"]
    seen = set()
    for name in names:
        cands = []
        for root in roots:
            if root is None:
                found = shutil.which(name)
                if found:
                    cands.append(found)
            elif root.endswith("Versions"):
                if name.startswith("python3."):
                    cands.append(f"{root}/{name[6:]}/bin/{name}")
            else:
                cands.append(f"{root}/{name}")
        for c in cands:
            real = str(Path(c).resolve()) if Path(c).exists() else c
            if real in seen or not Path(c).exists():
                continue
            seen.add(real)
            v = _version_of(c)
            if v and (3, 9) <= v[:2] <= cap:
                return c, (f"this Python is {platform.python_version()}, too new "
                           f"here ({why}); using {'.'.join(map(str, v))} at {c}")
    return None, (f"this Python is {platform.python_version()} and {why}. "
                  f"No Python between 3.9 and {cap[0]}.{cap[1]} was found.")


YUNET = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
         "models/face_detection_yunet/face_detection_yunet_2023mar.onnx")

STEP = 0


def say(msg: str) -> None:
    global STEP
    STEP += 1
    print(f"\n== {STEP}. {msg}", flush=True)


def info(msg: str) -> None:
    print(f"   {msg}", flush=True)


def die(msg: str, *hints: str):
    print(f"\n!! {msg}", file=sys.stderr)
    for h in hints:
        print(f"   {h}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


# ---------------------------------------------------------------- ffmpeg

def ffmpeg_lacks(exe: str) -> list[str]:
    """Which required filters or encoders this ffmpeg does NOT have."""
    def names(flag: str) -> set:
        try:
            out = subprocess.run([exe, "-hide_banner", flag], capture_output=True,
                                 text=True, timeout=60).stdout
        except Exception:                                       # noqa: BLE001
            return set()
        return {ln.split()[1] for ln in out.splitlines()
                if ln.startswith(" ") and len(ln.split()) > 1}
    have_f, have_e = names("-filters"), names("-encoders")
    if not have_f and not have_e:
        return ["(this ffmpeg would not answer -filters)"]
    return ([f for f in NEED_FILTERS if f not in have_f]
            + [e for e in NEED_ENCODERS if e not in have_e])


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (clean-cut setup)"})
    with urllib.request.urlopen(req, timeout=600) as r, dest.open("wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r   {done / 1048576:6.0f} / {total / 1048576:.0f} MB",
                      end="", flush=True)
        if total:
            print(flush=True)


def extract_ffmpeg(archive: Path, kind: str, into: Path) -> Path:
    """Pull just the ffmpeg binary out, wherever the archive buried it."""
    into.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if kind == "zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        else:
            # filter="data" refuses absolute paths and ../ escapes. Default on
            # 3.14, a warning before it, so ask for it explicitly.
            with tarfile.open(archive) as t:
                try:
                    t.extractall(tmp, filter="data")
                except TypeError:                               # python < 3.11.4
                    t.extractall(tmp)
        found = [p for p in tmp.rglob(f"ffmpeg{P.EXE}") if p.is_file()]
        if not found:
            raise RuntimeError(f"no ffmpeg inside {archive.name}")
        target = into / f"ffmpeg{P.EXE}"
        shutil.copy2(max(found, key=lambda p: p.stat().st_size), target)
    target.chmod(0o755)
    return target


def ensure_ffmpeg() -> str:
    say("ffmpeg")
    ours = P.private_ffmpeg()
    if ours.exists() and not ffmpeg_lacks(str(ours)):
        info(f"using the one installed here: {ours}")
        return str(ours)

    system = shutil.which("ffmpeg")
    if system:
        missing = ffmpeg_lacks(system)
        if not missing:
            ver = subprocess.run([system, "-hide_banner", "-version"],
                                 capture_output=True, text=True).stdout.splitlines()
            info(f"already installed: {ver[0] if ver else system}")
            return system
        info(f"{system} is missing: {', '.join(missing)}")
        info("that build cannot finish a render, so fetching a complete one")
    else:
        info("not installed on this machine")

    key = (platform.system(), platform.machine())
    if key not in FFMPEG_BUILDS:
        die(f"No static ffmpeg listed for {key[0]}/{key[1]}.",
            "Install ffmpeg yourself with every one of:",
            "  " + ", ".join(NEED_FILTERS + NEED_ENCODERS),
            "then run this again.")
    urls, kind = FFMPEG_BUILDS[key]
    info(f"downloading a complete build for {key[0]} {key[1]}")
    info("no administrator password needed; it goes in ~/.clean-cut/bin")
    got = None
    failures = []
    with tempfile.TemporaryDirectory() as td:
        for n, url in enumerate(urls):
            arc = Path(td) / f"ffmpeg-{n}.{kind}"
            try:
                download(url, arc)
                got = extract_ffmpeg(arc, kind, P.BIN_DIR)
            except Exception as e:                              # noqa: BLE001
                failures.append(f"{url} -> {e}")
                if n + 1 < len(urls):
                    info(f"that source did not answer ({e}); trying another")
                continue
            if n:
                info(f"the usual source was unreachable; used {url}")
            break
    if got is None:
        die("could not fetch ffmpeg from any source.",
            *[f"  {f}" for f in failures],
            f"Download one by hand and put the ffmpeg binary in {P.BIN_DIR}")

    missing = ffmpeg_lacks(str(got))
    if missing:
        die(f"the downloaded ffmpeg is also missing: {', '.join(missing)}",
            "This should not happen - please report it with the line above.")
    ver = subprocess.run([str(got), "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout.splitlines()
    info(f"installed: {ver[0] if ver else got}")
    return str(got)


# ---------------------------------------------------------------- python

def preflight() -> None:
    """Everything that can be known before anything is downloaded.

    Order matters. Debian and Ubuntu ship `python3` without the `venv` module -
    it is a separate package - so the install used to get as far as fetching
    ffmpeg and building nothing before failing on a step the person could have
    fixed in ten seconds. Fail in the first two seconds instead.
    """
    say("checking what this machine already has")
    if sys.version_info < (3, 9):
        die(f"This Python is {platform.python_version()}; 3.9 or newer is needed.")
    info(f"python {platform.python_version()}  ({sys.executable})")

    chosen, note = usable_interpreter()
    if chosen is None:
        cap, _ = py_ceiling()
        die(note,
            "Install one and run this again. The easiest route:",
            f"  https://www.python.org/downloads/release/python-3{cap[1]}0/",
            "Pick the macOS 64-bit Intel installer.",
            "Nothing needs uninstalling; they sit side by side.")
    if note:
        info(note)

    try:
        import venv                                            # noqa: F401,PLC0415
    except ImportError:
        die("this Python cannot create virtual environments at all.",
            "Debian or Ubuntu:   sudo apt install python3-venv",
            "Fedora:             sudo dnf install python3-devel")
    try:
        import ensurepip                                       # noqa: F401,PLC0415
        info("virtual environments: available")
    except ImportError:
        # Debian and Ubuntu split ensurepip out of the standard library, so a
        # stock cloud image cannot `python3 -m venv` a usable environment.
        # That used to be a `sudo apt install python3-venv` the person had to
        # go and run. It does not have to be: `venv --without-pip` still works
        # without it, and pip can be bootstrapped into the result. Tested on a
        # bare Ubuntu 24.04 image - no root, no package manager, pip 26.2.2.
        info("virtual environments: available (pip will be bootstrapped)")

    free = shutil.disk_usage(P.HOME_DIR.parent).free / 1073741824
    info(f"free disk: {free:.0f} GB" + ("" if free >= 5 else "  - NEEDS about 3.5 GB"))
    if free < 3.5:
        die(f"only {free:.1f} GB free; this needs about 3.5 GB.")


def ensure_venv() -> Path:
    say("python environment")
    py = P.venv_python()
    base, _ = usable_interpreter()
    if base is None:                     # preflight already explained and died
        die("no usable python; see the message above")

    cap, why = py_ceiling()
    if py.exists() and cap is not None:
        # A previous run may have built this environment with a Python that
        # cannot install the packages. Leaving it in place means every retry
        # fails the same way forever, so replace it rather than reuse it.
        have = _version_of(str(py))
        if have and have[:2] > cap:
            info(f"the existing environment is python {'.'.join(map(str, have))}, "
                 f"which cannot work here ({why}) - rebuilding it")
            shutil.rmtree(P.VENV_DIR, ignore_errors=True)

    if not py.exists():
        info(f"creating a private environment in {P.VENV_DIR}")
        try:
            # Swallow the output: when this fails on Debian or Ubuntu, venv
            # prints its own several-line "you need sudo apt install
            # python3-venv" notice, which flatly contradicts what we do next.
            run([base, "-m", "venv", str(P.VENV_DIR)],
                capture_output=True)
        except subprocess.CalledProcessError:
            # Almost always a Debian/Ubuntu box with no ensurepip. Build the
            # environment without pip and put pip in afterwards, so nobody has
            # to go and find a package manager and a password.
            info("no bundled pip here - building without it and adding pip")
            shutil.rmtree(P.VENV_DIR, ignore_errors=True)
            try:
                run([base, "-m", "venv", "--without-pip", str(P.VENV_DIR)])
            except subprocess.CalledProcessError:
                die("could not create a virtual environment.",
                    "Debian or Ubuntu:  sudo apt install python3-venv")
            bootstrap_pip(py)
    if not py.exists():
        die(f"the environment was created but {py} is not there.")
    if subprocess.run([str(py), "-m", "pip", "--version"],
                      capture_output=True).returncode != 0:
        bootstrap_pip(py)
    info(f"interpreter: {py}")
    return py


def bootstrap_pip(py: Path) -> None:
    """Put pip into an environment that was built without one."""
    info("fetching pip")
    with tempfile.TemporaryDirectory() as td:
        getpip = Path(td) / "get-pip.py"
        try:
            download("https://bootstrap.pypa.io/get-pip.py", getpip)
            run([str(py), str(getpip), "-q"])
        except Exception as e:                                  # noqa: BLE001
            die(f"could not install pip: {e}",
                "Debian or Ubuntu:  sudo apt install python3-venv",
                "then delete ~/.clean-cut/venv and run this again.")
    info("pip installed")


def ensure_packages(py: Path) -> None:
    say("python packages")
    # opencv is pinned: 4.x has FaceDetectorYN, 5.x removed it.
    pkgs = ["pillow", "opencv-python-headless>=4.9,<5", "pyflakes"]
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        info("Apple Silicon -> mlx-whisper (the fastest local transcription)")
        pkgs.append("mlx-whisper")
    else:
        info(f"{platform.system()} {platform.machine()} -> faster-whisper")
        pkgs.append("faster-whisper")
    run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    info("installing (about 1.4 GB, a few minutes on a first run)")
    run([str(py), "-m", "pip", "install", "-q", *pkgs])
    info("done")


# ---------------------------------------------------------------- assets

def ensure_face_model() -> None:
    say("face-screening model")
    model = HERE / "yunet.onnx"
    if model.exists() and model.stat().st_size > 100_000:
        info(f"present ({model.stat().st_size // 1024} KB)")
        return
    info("fetching YuNet (MIT)")
    try:
        download(YUNET, model)
    except Exception as e:                                      # noqa: BLE001
        die(f"could not fetch the face model: {e}")
    # The plain raw.githubusercontent URL returns a ~130 byte Git LFS pointer
    # rather than the model. Guard against that poisoning the face screen.
    if model.stat().st_size < 100_000:
        size = model.stat().st_size
        model.unlink(missing_ok=True)
        die(f"the download returned {size} bytes, not a model (an LFS pointer).")
    info(f"installed ({model.stat().st_size // 1024} KB)")


def ensure_broll(py: Path) -> bool:
    say("b-roll library")
    info("the screened clips the twelve scripts reference (about 403 MB)")
    info("this cannot be re-harvested: a fresh harvest returns different")
    info("footage under the same filenames, and the screening is the value")
    r = subprocess.run([str(py), str(HERE / "fetch_broll.py"),
                        "--out", str(P.BROLL_DIR)])
    if r.returncode != 0:
        info("incomplete - re-run this script, it skips what is verified")
        info("or see reference/broll-sources.md to fetch them in a browser")
    return r.returncode == 0


# ---------------------------------------------------------------- verify

def verify(py: Path) -> int:
    say("checking this machine")
    return subprocess.run([str(py), str(HERE / "doctor.py")]).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Install everything clean-cut needs.")
    ap.add_argument("--skip-broll", action="store_true",
                    help="leave the b-roll library for later")
    args = ap.parse_args()

    print("clean-cut setup")
    print(f"   {platform.system()} {platform.machine()}, "
          f"python {platform.python_version()}")
    print(f"   everything goes in {P.HOME_DIR}")

    P.HOME_DIR.mkdir(parents=True, exist_ok=True)
    preflight()
    ensure_ffmpeg()
    P.use_private_tools()
    py = ensure_venv()
    ensure_packages(py)
    ensure_face_model()
    if not args.skip_broll:
        ensure_broll(py)

    rc = verify(py)
    say("ready")
    info(f"python : {py}")
    info(f"library: {P.BROLL_DIR}")
    info("the speech model (about 1.5 GB) downloads on the first real edit")
    print()
    # Print the SAME command the installer prints. run.py re-executes itself
    # into the venv, so plain python3 is correct and is one less path for a
    # person to mistype; showing two different commands back to back just
    # makes them wonder which one is right.
    info("To edit a video:")
    info(f"   python3 {HERE / 'run.py'} \"your-video.mp4\"")
    if rc != 0:
        print("\n   Setup finished, but the checks above found blocking problems.")
        print("   Fix those before editing a video, then run this again.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
