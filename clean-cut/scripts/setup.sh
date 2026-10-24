#!/usr/bin/env bash
# setup.sh - macOS and Linux entry point. Windows uses setup.ps1.
#
#   bash scripts/setup.sh
#
# Deliberately almost empty. All of the work is in setup.py, so there is one
# implementation of the setup rather than one per platform; this file only
# finds a Python and hands over. The previous version did the whole install in
# bash, which meant Windows had no installer at all.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "clean-cut needs Python 3.9 or newer, and this machine has none on PATH."
  echo
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "  macOS ships one. Open Terminal and run:   xcode-select --install"
    echo "  Or install from https://www.python.org/downloads/macos/"
  else
    echo "  Debian or Ubuntu:   sudo apt install python3 python3-venv"
    echo "  Fedora:             sudo dnf install python3"
  fi
  echo
  echo "Then run this again. Nothing else is needed - setup fetches ffmpeg itself."
  exit 1
fi

exec "$PY" "$HERE/setup.py" "$@"
