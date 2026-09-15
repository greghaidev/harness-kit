#!/usr/bin/env python3
"""to_pdf.py — render a built HTML document to PDF with a headless Chrome, Chromium or Edge.

    python to_pdf.py <input.html> [output.pdf]

Deliberately NOT Playwright: the repository this came from forbids Playwright on the daily
driver unconditionally. This is a single-shot headless print — no npm, no test runner, no
browser automation. The print stylesheet in press_css/press_economist is a real layout
(@page letter, its own millimetre measure, break-inside: avoid on every figure and code
block), so the browser's own print path is the renderer the design was built for.

It was a bash script that only knew the Linux names for Chrome. It now looks in the places a
browser actually lives on Windows and macOS too, and Microsoft Edge counts: it ships with
Windows and prints with the same engine. Set PRESS_BROWSER to use a specific executable.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PATH_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "chrome", "msedge", "microsoft-edge", "microsoft-edge-stable")

MAC_APPS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")

WINDOWS_APPS = (r"Google\Chrome\Application\chrome.exe",
                r"Microsoft\Edge\Application\msedge.exe",
                r"Chromium\Application\chrome.exe")


def find_browser():
    """An executable path, or None. PRESS_BROWSER wins; then PATH; then the usual install spots."""
    override = os.environ.get("PRESS_BROWSER")
    if override:
        return override if (Path(override).is_file() or shutil.which(override)) else None
    for name in PATH_NAMES:
        hit = shutil.which(name)
        if hit:
            return hit
    candidates = list(MAC_APPS)
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates += [str(Path(base) / rel) for rel in WINDOWS_APPS]
    return next((c for c in candidates if Path(c).is_file()), None)


def find_ghostscript():
    return shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")


def page_count(pdf: bytes) -> int:
    """/Count in the page tree, else count page objects. Both survive compression of CONTENT
    streams, because the object dictionaries themselves stay plain."""
    counts = re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", pdf, re.S)
    return max((int(x) for x in counts), default=len(re.findall(rb"/Type\s*/Page\b", pdf)))


def human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n}"


def compress(pdf: Path, gs: str) -> bool:
    """Chrome re-encodes every embedded image on print, so a document with 30 plates lands around
    25MB. Ghostscript re-compresses to roughly an eighth of that with no visible loss at reading
    size. On any failure the uncompressed file is kept — it is still correct, just heavy."""
    raw = pdf.with_suffix(".raw.pdf")
    pdf.replace(raw)
    r = subprocess.run([gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", "-dPDFSETTINGS=/ebook",
                        "-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={pdf}", str(raw)],
                       capture_output=True)
    if r.returncode == 0 and pdf.is_file() and pdf.stat().st_size > 0:
        raw.unlink()
        return True
    raw.replace(pdf)
    return False


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: to_pdf.py <input.html> [output.pdf]", file=sys.stderr)
        return 2
    html = Path(argv[0]).resolve()
    pdf = Path(argv[1]).resolve() if len(argv) > 1 else html.with_suffix(".pdf")
    if not html.is_file():
        print(f"to_pdf: no such file: {html}", file=sys.stderr)
        return 1
    browser = find_browser()
    if not browser:
        print("to_pdf: no Chrome, Chromium or Edge found — install one, or set PRESS_BROWSER "
              "to its executable. The HTML is still complete without the PDF.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([browser, "--headless", "--disable-gpu", "--no-sandbox",
                        f"--user-data-dir={profile}", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", html.as_uri()],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
    if not pdf.is_file() or pdf.stat().st_size == 0:
        print("to_pdf: the browser produced no output", file=sys.stderr)
        return 1

    gs = find_ghostscript()
    if gs and not compress(pdf, gs):
        print("to_pdf: ghostscript pass failed; keeping the uncompressed file", file=sys.stderr)

    print(f"wrote {pdf} — {human_size(pdf.stat().st_size)}, {page_count(pdf.read_bytes())} pages")
    return 0


if __name__ == "__main__":
    # A Windows pipe defaults to the ANSI code page; the em dash in the report line would crash it.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
