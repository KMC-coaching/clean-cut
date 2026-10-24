#!/usr/bin/env bash
# One paste, for macOS and Linux. Downloads clean-cut and installs everything.
#
#   curl -fsSL https://raw.githubusercontent.com/KMC-coaching/clean-cut/main/install.sh | bash
#
# No git needed, no administrator password, no package manager. Wrapped in a
# function and called at the end on purpose: bash reads a piped script as it
# runs, so a half-downloaded file would otherwise execute half an installer.

main() {
  set -euo pipefail

  REPO="KMC-coaching/clean-cut"
  DEST="${CLEAN_CUT_SRC:-$HOME/clean-cut}"
  URL="https://codeload.github.com/$REPO/tar.gz/refs/heads/main"

  say()  { printf '\n== %s\n' "$1"; }
  info() { printf '   %s\n' "$1"; }
  bad()  { printf '\n!! %s\n' "$1" >&2; }

  echo "clean-cut installer"
  info "$(uname -s) $(uname -m)"

  # ---------------------------------------------------------------- python
  say "python"
  PY=""
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    bad "clean-cut needs Python 3.9 or newer, and this machine has none."
    if [ "$(uname -s)" = "Darwin" ]; then
      info "Run this, let it finish, then paste the install command again:"
      info "    xcode-select --install"
    else
      info "Debian or Ubuntu:  sudo apt install python3"
      info "Fedora:            sudo dnf install python3"
    fi
    exit 1
  fi
  info "$($PY -V 2>&1)"

  # ---------------------------------------------------------------- download
  say "downloading clean-cut"
  if [ -d "$DEST" ]; then
    info "replacing the previous copy at $DEST"
    rm -rf "$DEST.old" && mv "$DEST" "$DEST.old"
  fi
  mkdir -p "$DEST"
  # --fail so an HTML error page is never unpacked as if it were the program.
  if ! curl -fsSL "$URL" | tar xz -C "$DEST" --strip-components=1; then
    bad "could not download from $URL"
    info "Check the internet connection and try again."
    [ -d "$DEST.old" ] && { rm -rf "$DEST"; mv "$DEST.old" "$DEST"; info "put the previous copy back"; }
    exit 1
  fi
  rm -rf "$DEST.old"
  info "into $DEST"

  # ---------------------------------------------------------------- install
  say "installing (this is the long part - 20 minutes or so)"
  info "it fetches ffmpeg, python packages and the b-roll clips"
  info "nothing here asks for a password"
  bash "$DEST/clean-cut/scripts/setup.sh"

  # ---------------------------------------------------------------- finish
  REF="$DEST/clean-cut/reference"
  MISSING=""
  for f in master-scripts.json script-broll.json; do
    [ -f "$REF/$f" ] || MISSING="$MISSING $f"
  done

  say "done"
  echo
  echo "   To edit a video, paste this and change the filename:"
  echo
  echo "       $PY $DEST/clean-cut/scripts/run.py \"my-video.mp4\""
  echo
  echo "   The finished file appears next to the original, named"
  echo "   my-video-edited.mp4."
  if [ -n "$MISSING" ]; then
    echo
    echo "   ONE THING IS STILL MISSING. Ask whoever sent you this for:"
    for f in $MISSING; do echo "       $f"; done
    echo "   and save them into:"
    echo "       $REF"
    echo
    echo "   Without them it still edits your video - trimming, captions,"
    echo "   sound - but it cannot recognise which script you recorded, so"
    echo "   it will not add any b-roll."
  fi
  echo
}

main "$@"
