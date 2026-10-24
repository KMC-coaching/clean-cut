# clean-cut

Turns a raw talking-head video into a finished vertical cut: dead air trimmed,
fumbled takes and countdown false starts removed, captions burned in, b-roll
dropped over the lines it was chosen for, audio levelled for social.

It runs **entirely on your own computer**. Nothing is uploaded, there is no
account and no subscription, and no per-video cost.

## Install it

**Windows** — open PowerShell (press Start, type `powershell`, Enter) and paste:

```powershell
irm https://raw.githubusercontent.com/KMC-coaching/clean-cut/main/install.ps1 | iex
```

**macOS or Linux** — open Terminal (⌘-Space, type `terminal`, Enter) and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/KMC-coaching/clean-cut/main/install.sh | bash
```

That is the whole thing. It needs no administrator password and no package
manager: it fetches Python if you have none, fetches ffmpeg, builds its own
private environment, downloads the b-roll, checks the machine, and then prints
the exact command to edit a video.

Allow about twenty minutes and roughly 3.5 GB of space. Most of it is
downloading, so you can leave it running.

## Then edit a video

The installer prints this with the right path filled in:

```bash
python3 ~/clean-cut/clean-cut/scripts/run.py "my-video.mp4"
```

The finished file lands next to the original as `my-video-edited.mp4`.

`START-HERE.md` has the rest: what to do when something goes wrong, how much
memory a render wants, and what the tool deliberately will not do.

## Two files are not in here

Script recognition and b-roll placement need `master-scripts.json` and
`script-broll.json`, which are the client's own scripts and are not public.
Ask whoever sent you this for them and drop them into
`clean-cut/reference/`. Without them everything else still works — trimming,
captions, sound, stabilisation — but no b-roll is placed.

## Platform tests

`.github/workflows/clean-cut-platforms.yml` installs and runs a real edit on
hosted Windows, Intel macOS and Linux runners. It carries no client content
and no recordings of anyone: CI writes an invented script and three ffmpeg
test patterns, and speaks them through the operating system's own
text-to-speech.
