#!/usr/bin/env bash
# Kept so installs whose settings.json already points here keep working. The Stop guard is
# stop_guard.py, which runs the same on Windows, macOS and Linux; new installs call it directly.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/../../.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$HERE/stop_guard.py"
