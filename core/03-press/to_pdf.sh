#!/usr/bin/env bash
# Render a built HTML document to PDF.
#
# Deliberately NOT Playwright: this repo forbids Playwright on the daily driver
# unconditionally. This is a single-shot headless print — no npm, no test runner,
# no browser automation. The print stylesheet in press_css/press_economist is a
# real layout (@page letter, its own millimetre measure, break-inside: avoid on
# every figure and code block), so the browser's own print path is the renderer
# the design was built for.
set -euo pipefail
HTML="${1:?usage: to_pdf.sh <input.html> [output.pdf]}"
PDF="${2:-${HTML%.html}.pdf}"
ABS="$(readlink -f "$HTML")"
CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
[ -n "$CHROME" ] || { echo "to_pdf.sh: no chrome/chromium on PATH" >&2; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
"$CHROME" --headless --disable-gpu --no-sandbox \
  --user-data-dir="$TMP" \
  --no-pdf-header-footer \
  --print-to-pdf="$PDF" "file://$ABS" >/dev/null 2>&1
[ -s "$PDF" ] || { echo "to_pdf.sh: produced no output" >&2; exit 1; }
PAGES="$(python3 - "$PDF" <<'PYEOF'
import re, sys, zlib
b = open(sys.argv[1], "rb").read()
# /Count in the page tree, else count page objects; both survive compression of
# CONTENT streams because the object dictionaries themselves stay plain here.
m = re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", b, re.S)
print(max((int(x) for x in m), default=len(re.findall(rb"/Type\s*/Page\b", b))))
PYEOF
)"
echo "wrote $PDF — $(du -h "$PDF" | cut -f1), $PAGES pages"
