#!/usr/bin/env bash
# smoke.sh - kept so the documented command still works. The checks moved to
# selftest.py, which runs on Windows too; this one did not, so "did I break
# it" was a question only a Mac or Linux box could ask.
#
#   bash scripts/smoke.sh                # static, path and windows-shape checks
#   bash scripts/smoke.sh <any video>    # the above plus a real 14 second edit

set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY="$HOME/.clean-cut/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"

exec "$PY" "$HERE/selftest.py" "$@"
