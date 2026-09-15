#!/usr/bin/env bash
# Kept for callers that still run this script (book/build.sh). The renderer is to_pdf.py, which
# finds Chrome, Chromium or Edge on Windows, macOS and Linux alike.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/to_pdf.py" "$@"
