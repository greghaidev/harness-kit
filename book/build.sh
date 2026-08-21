#!/usr/bin/env bash
# Build the kit's book. The press is core/03-press; the claim gate resolves against the
# repository root, so every count and quoted symbol in the book is read off this tree.
set -euo pipefail
cd "$(dirname "$0")"
KIT="$(cd .. && pwd)"
PRESS_ART_DIR="$(pwd)" PYTHONPATH="$KIT/core/03-press" python3 "$KIT/core/03-press/press.py" book.md \
  --out book.html \
  --title "The Harness" \
  --wordmark "Field Notes" \
  --eyebrow "An agent harness that remembers what it concluded" \
  --standfirst-label "The short version" \
  --standfirst "Claude Code forgets everything between sessions. This is what it takes to make it stop — and what that buys once it has." \
  --repo-root "$KIT"
bash "$KIT/core/03-press/to_pdf.sh" book.html
