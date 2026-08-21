#!/usr/bin/env python3
"""The claim gate — every reader-facing fact about this repo resolves against the repo.

The press this came from binds its numbers to a fact registry and fails the build when one
stops tracing. This is the same idea pointed at a different substrate: a book about a
codebase has no dataset, it has the codebase, so the registry IS the working tree.

The rule that motivates the whole module, learned the expensive way on that report:

    A claim outside the gate is a claim outside the gate.

Two reader-facing claims lived in that BUILDER instead of its gated markdown and
shipped wrong for weeks — through three heavy Fact-Check rounds and nine reviewer passes —
because every one of those rounds was pointed at the markdown. So nothing here may be typed
by hand into prose: a line count is a token, not a numeral, and the day the file grows the
sentence restates itself instead of going quietly stale.

TOKENS

    {{path:scripts/lane.py}}            renders `scripts/lane.py`; FAILS if absent
    {{lines:scripts/lane.py}}           renders 2,300 (live); FAILS if absent
    {{count:.claude/hooks/*.sh}}        renders the glob's match count
    {{exists:.githooks/pre-commit}}     renders nothing; a bare assertion
    {{quote:path#SYMBOL}}               renders the live source of a def/class/CONSTANT
    {{contains:path#some literal}}      renders nothing; FAILS if the literal is absent

`contains` is the escape hatch for a claim about a file whose shape no other verb captures
("the blocklist still refuses TRUNCATE"). It renders nothing and only asserts, so it can be
dropped mid-sentence without disturbing the prose.

FAILURE IS COLLECTED, NOT RAISED. resolve() returns every problem it found, because an
author fixing a book wants the whole list, not the first item ten times.
"""
from __future__ import annotations

import os
import pathlib
import re
from typing import Callable

TOKEN = re.compile(r"\{\{(path|lines|count|exists|quote|contains):(.+?)\}\}", re.S)

# A quote token names a Python symbol: `def foo`, `class Foo`, or `FOO = ...`.
# Line ranges were considered and rejected: prose outlives line numbers, and a
# range that silently slides is worse than no quote at all.
_SYMBOL_START = "^(?:def {s}\\b|class {s}\\b|{s}\\s*(?::[^=\\n]+)?=)"


class ClaimError(Exception):
    pass


# Absolute roots a claim may reach outside the repo. The memory store is a separate git
# repository at a system path, so a book about this harness cannot make a single true
# statement about the store without leaving the working tree. Everything else is still
# refused: this is an allowlist, not an escape hatch.
EXTERNAL_ROOTS = tuple(
    pathlib.Path(os.path.expanduser(p))
    for p in os.environ.get("PRESS_EXTERNAL_ROOTS", "").split(":") if p
)


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _permitted(p: pathlib.Path, root: pathlib.Path) -> bool:
    if p.is_relative_to(root.resolve()):
        return True
    return any(p.is_relative_to(er) for er in EXTERNAL_ROOTS)


def _resolve_path(root: pathlib.Path, rel: str) -> pathlib.Path:
    raw = pathlib.Path(rel)
    p = (raw if raw.is_absolute() else root / rel).resolve()
    # Refuse anything outside the repo and outside the allowlist. A book that quotes
    # /etc/shadow is not a book.
    if not _permitted(p, root):
        raise ClaimError(f"path escapes the permitted roots: {rel!r}")
    return p


def _extract_symbol(text: str, symbol: str) -> str:
    """Return the source block for `symbol`, dedented to its own indent level.

    A block ends at the first subsequent line that is non-blank and indented no
    further than the opening line. That is the whole grammar; it is enough for
    top-level defs, classes and constant assignments, which is all a book quotes.
    """
    pat = re.compile(_SYMBOL_START.format(s=re.escape(symbol)), re.M)
    m = pat.search(text)
    if not m:
        raise ClaimError(f"symbol {symbol!r} not found")
    lines = text[m.start():].split("\n")
    opening_indent = len(lines[0]) - len(lines[0].lstrip())
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= opening_indent:
            break
        out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _handle_path(root, arg, esc):
    p = _resolve_path(root, arg)
    if not p.exists():
        raise ClaimError(f"no such path: {arg}")
    return f"<code>{esc(arg)}</code>"


def _handle_lines(root, arg, esc):
    p = _resolve_path(root, arg)
    if not p.is_file():
        raise ClaimError(f"no such file: {arg}")
    n = len(p.read_text(errors="replace").split("\n"))
    # A trailing newline yields a final empty element; a file's "line count" in
    # every tool a reader would check against (wc -l) does not count it.
    if p.read_text(errors="replace").endswith("\n"):
        n -= 1
    return _fmt_int(n)


def _handle_count(root, arg, esc):
    raw = pathlib.Path(arg)
    if raw.is_absolute():
        # pathlib refuses an absolute pattern; anchor it on its own root and check the
        # allowlist, so an absolute glob is as constrained as an absolute path.
        base = pathlib.Path(raw.anchor)
        pattern = str(raw.relative_to(base))
        if not any(pathlib.Path(raw.parent).is_relative_to(er) for er in EXTERNAL_ROOTS):
            raise ClaimError(f"glob escapes the permitted roots: {arg}")
        matches = sorted(base.glob(pattern))
    else:
        matches = sorted(root.glob(arg))
    if not matches:
        raise ClaimError(f"glob matched nothing: {arg}")
    return _fmt_int(len(matches))


def _handle_exists(root, arg, esc):
    p = _resolve_path(root, arg)
    if not p.exists():
        raise ClaimError(f"no such path: {arg}")
    return ""


def _handle_quote(root, arg, esc):
    if "#" not in arg:
        raise ClaimError(f"quote needs path#SYMBOL, got {arg!r}")
    rel, symbol = arg.split("#", 1)
    p = _resolve_path(root, rel.strip())
    if not p.is_file():
        raise ClaimError(f"no such file: {rel.strip()}")
    block = _extract_symbol(p.read_text(errors="replace"), symbol.strip())
    return f'<pre class="src"><code>{esc(block)}</code></pre>'


def _handle_contains(root, arg, esc):
    if "#" not in arg:
        raise ClaimError(f"contains needs path#literal, got {arg!r}")
    rel, literal = arg.split("#", 1)
    p = _resolve_path(root, rel.strip())
    if not p.is_file():
        raise ClaimError(f"no such file: {rel.strip()}")
    if literal not in p.read_text(errors="replace"):
        raise ClaimError(f"{rel.strip()} no longer contains {literal!r}")
    return ""


_HANDLERS: dict[str, Callable] = {
    "path": _handle_path,
    "lines": _handle_lines,
    "count": _handle_count,
    "exists": _handle_exists,
    "quote": _handle_quote,
    "contains": _handle_contains,
}


def resolve(text: str, root: pathlib.Path, esc: Callable[[str], str] | None = None):
    """Substitute every claim token. Returns (text, problems).

    `esc` is the caller's HTML escaper; passing it in keeps this module free of any
    opinion about the output format, so the same gate can check a plain-markdown
    draft before the press is involved.
    """
    if esc is None:
        esc = lambda s: s  # noqa: E731
    problems: list[str] = []
    seen = 0

    def sub(m):
        nonlocal seen
        seen += 1
        verb, arg = m.group(1), m.group(2).strip()
        try:
            return _HANDLERS[verb](root, arg, esc)
        except Exception as e:
            # Deliberately broad. A handler that raises something unexpected must degrade
            # into a REPORTED broken claim, never abort the build — otherwise one malformed
            # token in a 200-page document destroys the whole run with a stack trace, and
            # the author cannot see the other nineteen problems.
            problems.append(f"{{{{{verb}:{arg}}}}} — {e}")
            # Leave a visible scar. A silently-dropped claim is how a gate becomes
            # a comment; if someone ships despite the non-zero exit, the page says so.
            return f'<span class="claim-broken">[BROKEN CLAIM: {esc(str(e))}]</span>'

    return TOKEN.sub(sub, text), problems


def count_tokens(text: str) -> int:
    """How many claims this document makes. The denominator for the gate's report."""
    return len(TOKEN.findall(text))
