#!/usr/bin/env bash
# Run the engine's tests. The overlay test needs PySide6 and skips without it,
# so look for the venv the installer built before falling back to bare python3.
#
#   ./run-tests.sh              all tests
#   PET_PYTHON=... ./run-tests.sh   with a chosen interpreter
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLED="$(cat "$HOME/.assistant-pet/engine" 2>/dev/null || true)"

PY="${PET_PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in "$ROOT/.venv/bin/python" \
                   "${INSTALLED:-/nonexistent}/.venv/bin/python" \
                   "$(dirname "${INSTALLED:-/nonexistent}")/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
      PY="$candidate"
      break
    fi
  done
fi
[ -n "$PY" ] || PY="$(command -v python3)"
echo "python: $PY"

export QT_QPA_PLATFORM=offscreen
fail=0
for t in "$ROOT"/engine/tests/test_*.py; do
  echo "==> $(basename "$t")"
  "$PY" "$t" || fail=1
done
exit "$fail"
