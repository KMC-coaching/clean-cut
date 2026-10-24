# clean-cut platform tests

This repo exists for one job: proving the `clean-cut` video editor installs
and runs on machines that are not the one it was written on — real Windows,
real Intel macOS, real Linux — using GitHub's hosted runners.

**It is not where clean-cut is developed.** The source of truth is
`Projects/clean-cut` in Jerry's work repo. The `clean-cut/` folder here is a
copy of that code, pushed when a platform run is wanted, and should never be
edited here.

## What is deliberately missing

No master scripts and no b-roll. Those are the client's twelve scripts and
403 MB of licensed Pexels footage, and neither belongs in a repository whose
only purpose is running tests. `.github/scripts/make_fixtures.py` writes an
invented script and three ffmpeg test patterns instead, so every run still
exercises script identification, b-roll placement, and the whole
zoompan/alphamerge/overlay chain — against content nobody owns.

No sample video either: the real student uploads are recordings of
identifiable people. `make_test_video.py` speaks the fixture script through
the operating system's own text-to-speech, so each runner edits real speech
and nobody is in it.

## Running it

Actions → **clean-cut platforms** → Run workflow, and pick a platform group.
Manual only, on purpose: on a private repo macOS bills at 10x and Windows at
2x, so this does not run on push.

Each run uploads the finished video, its decision report, and three frames,
so the result can be looked at rather than inferred from a passing audit.
