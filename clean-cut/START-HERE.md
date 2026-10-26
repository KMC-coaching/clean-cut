# clean-cut — start here

Turns a raw talking-head video into a finished vertical cut: dead air trimmed,
fumbled takes and countdown false starts removed, captions burned in, b-roll
dropped over the lines it was chosen for, audio levelled for social.

It runs **entirely on your own computer**. Nothing is uploaded, there is no
account and no subscription, and no per-video cost.

---

## Setting it up

You need about **3.5 GB of free space** and twenty minutes, most of it
downloads you can walk away from. You do **not** need to be an administrator,
and you do **not** need to install ffmpeg, Homebrew, or anything else by hand —
setup fetches what is missing.

### Windows

Open **PowerShell** (press Start, type `powershell`, press Enter), then:

```powershell
cd path\to\clean-cut
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

If Python is missing it offers to install it for you and you answer `Y`. That
install goes into your own user account, so it will not ask for a password.

> `-ExecutionPolicy Bypass` applies to that one command only. It does not
> change any setting on your machine.

### macOS

Open **Terminal** (press ⌘-Space, type `terminal`, press Enter), then:

```bash
cd path/to/clean-cut
bash scripts/setup.sh
```

If it says Python is missing, run `xcode-select --install`, let that finish,
and run the setup again.

### Linux

```bash
cd path/to/clean-cut
bash scripts/setup.sh
```

On Debian or Ubuntu you may first be told to run
`sudo apt install python3-venv`. That is the **only** command in the whole
setup that needs a password, and setup tells you within two seconds rather
than after a download.

---

## Editing a video

One command, the same on every platform:

```bash
python3 scripts/run.py "my-video.mp4"
```

On Windows use `python` instead of `python3` if `python3` is not recognised.

The finished file lands next to the original as `my-video-edited.mp4`. A
one-minute video takes a couple of minutes to edit; the very first run also
downloads the speech model (about 1.5 GB), so give that one longer.

Useful variations:

```bash
python3 scripts/run.py "my-video.mp4" --out finished.mp4   # choose the name
python3 scripts/run.py "my-video.mp4" --no-broll           # talking head only
```

---

## If something goes wrong

Run this first. It checks every single thing the editor needs and names
whatever is missing, in plain words:

```bash
python3 scripts/doctor.py
```

It is read-only and cannot break anything. If it ends with **"Ready"**, the
machine is fine and the problem is with the video or the edit — the table at
the end of `SKILL.md` covers those.

Two things worth knowing before you start:

- **Memory.** A render wants around 1 GB, plus roughly half a gigabyte for
  each b-roll insert. On a machine with 8 GB, close your browser first. On
  less than that, use `--no-broll`. `doctor.py` warns you.
- **It only knows the twelve standard scripts.** Given something else it says
  `UNIDENTIFIED` and still produces a clean talking-head cut — it just will
  not place b-roll, because a wrong clip is worse than no clip.

---

## What it will not do

It does not write your content or fix what you said. It cannot tell you an
insert is the *wrong* insert. It will not invent b-roll to hit a count: if the
line a clip was chosen for was not spoken, that clip is dropped rather than
moved somewhere it does not belong.

Everything it decided is written to a `.report.json` next to the finished
video, so when a result looks wrong you can see exactly which decision
produced it.
