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
FFMPEG_BUILDS = {
    ("Darwin", "arm64"):  ("https://www.osxexperts.net/ffmpeg711arm.zip", "zip"),
    ("Darwin", "x86_64"): ("https://www.osxexperts.net/ffmpeg711intel.zip", "zip"),
    ("Linux", "aarch64"): ("https://johnvansickle.com/ffmpeg/releases/"
                           "ffmpeg-release-arm64-static.tar.xz", "tar"),
    ("Linux", "x86_64"):  ("https://johnvansickle.com/ffmpeg/releases/"
                           "ffmpeg-release-amd64-static.tar.xz", "tar"),
    ("Windows", "AMD64"): ("https://www.gyan.dev/ffmpeg/builds/"
                           "ffmpeg-release-essentials.zip", "zip"),
    ("Windows", "ARM64"): ("https://www.gyan.dev/ffmpeg/builds/"
                           "ffmpeg-release-essentials.zip", "zip"),
}

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
    url, kind = FFMPEG_BUILDS[key]
    info(f"downloading a complete build for {key[0]} {key[1]}")
    info("no administrator password needed; it goes in ~/.clean-cut/bin")
    with tempfile.TemporaryDirectory() as td:
        arc = Path(td) / f"ffmpeg.{kind}"
        try:
            download(url, arc)
            got = extract_ffmpeg(arc, kind, P.BIN_DIR)
        except Exception as e:                                  # noqa: BLE001
            die(f"could not fetch ffmpeg: {e}",
                f"Download it by hand from {url}",
                f"and put the ffmpeg binary in {P.BIN_DIR}")

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
    if not py.exists():
        info(f"creating a private environment in {P.VENV_DIR}")
        try:
            # Swallow the output: when this fails on Debian or Ubuntu, venv
            # prints its own several-line "you need sudo apt install
            # python3-venv" notice, which flatly contradicts what we do next.
            run([sys.executable, "-m", "venv", str(P.VENV_DIR)],
                capture_output=True)
        except subprocess.CalledProcessError:
            # Almost always a Debian/Ubuntu box with no ensurepip. Build the
            # environment without pip and put pip in afterwards, so nobody has
            # to go and find a package manager and a password.
            info("no bundled pip here - building without it and adding pip")
            shutil.rmtree(P.VENV_DIR, ignore_errors=True)
            try:
                run([sys.executable, "-m", "venv", "--without-pip", str(P.VENV_DIR)])
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
    info("To edit a video:")
    info(f"   {py} {HERE / 'run.py'} \"your-video.mp4\"")
    if rc != 0:
        print("\n   Setup finished, but the checks above found blocking problems.")
        print("   Fix those before editing a video, then run this again.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
