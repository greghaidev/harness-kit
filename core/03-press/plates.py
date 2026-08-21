#!/usr/bin/env python3
"""Section plates — the illustrations that break up a long read.

Ported from a magazine-format report press, with one deliberate behaviour change: a MISSING
plate is a warning, not an error. The images for this book are produced by a
separate agent and land asynchronously, so the book has to build from the first
day of writing, with the plates appearing as they arrive. That press could fail hard because its
illustrations were commissioned before its prose.

The mapping is anchor -> key, and the anchor is derived from the SECTION HEADING
TEXT. That coupling bit once already: retitling a section silently orphaned its
plate, which is why that build grew a test asserting every section opens
on one. `missing_plates()` below is this book's version of that check — the
build reports orphans rather than discovering them in a PDF.
"""
from __future__ import annotations

import base64
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ART = HERE.parent
ILLO = ART / "illustrations" / "robots"
WEB = ILLO / "web"
MANIFEST = ART / "illustrations" / "plates.json"

# Anything the encoder produced, preferred in this order. WebP holds hard edges
# without the ringing JPEG puts on line art; PNG is the fallback for a plate that
# has not been through encode_plates.py yet.
_EXT = (".webp", ".png")

_MIME = {".webp": "image/webp", ".png": "image/png"}

_missing: list[str] = []


def _manifest() -> dict:
    if not MANIFEST.exists():
        return {"hero": None, "plates": {}}
    return json.loads(MANIFEST.read_text())


def _find(key: str) -> pathlib.Path | None:
    for base in (WEB, ILLO):
        for ext in _EXT:
            p = base / f"{key}{ext}"
            if p.exists():
                return p
    return None


def plate(key: str | None, hero: bool = False) -> str:
    """Return the plate markup for `key`, or an empty string if it has not landed.

    alt is deliberately empty: these are decorative. They carry nothing the prose
    does not, and announcing "an illustration of a robot" to a screen reader in
    the middle of a technical argument is noise, not access.
    """
    if not key:
        return ""
    p = _find(key)
    if p is None:
        _missing.append(key)
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()
    cls = "plate plate-hero" if hero else "plate"
    return (f'<div class="{cls}">'
            f'<img src="data:{_MIME[p.suffix]};base64,{b64}" alt="">'
            f"</div>")


def plate_for(anchor: str) -> str:
    return plate(_manifest().get("plates", {}).get(anchor))


def hero() -> str:
    return plate(_manifest().get("hero"), hero=True)


def missing_plates() -> list[str]:
    """Keys the manifest asked for that are not on disk yet."""
    return sorted(set(_missing))


def orphaned_anchors(seen_anchors: set[str]) -> list[str]:
    """Manifest entries pointing at a section that no longer exists.

    This is the retitling trap: the heading moves, the anchor changes, and the
    plate quietly stops rendering with nothing to say so.
    """
    return sorted(set(_manifest().get("plates", {})) - seen_anchors)


def available() -> list[str]:
    """Every plate key on disk — what the image agent has actually delivered."""
    keys = set()
    for base in (WEB, ILLO):
        if base.exists():
            for ext in _EXT:
                keys |= {p.stem for p in base.glob(f"*{ext}")}
    return sorted(keys)
