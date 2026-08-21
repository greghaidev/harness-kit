#!/usr/bin/env python3
"""The press — markdown in, a finished magazine-format document out.

Ported from a magazine-format report press and generalised. What survives is
the part that makes that report read the way it does: the two-font discipline, the
section furniture (plate, rule, kicker, headline), small-caps openers, end marks,
pull statements, the contents ribbon, and a print stylesheet that is a real layout
rather than an afterthought.

THREE THINGS DELIBERATELY CHANGED

1.  Pull statements are an EXPLICIT MARKER (`!! `), not a substring match against a
    list of sentence prefixes. The original matched prefixes, and its own README
    admits the consequence: "Edit a sentence in the markdown and its pull styling
    disappears with no error." It needed a test to catch what a marker makes
    impossible.

2.  Fenced CODE BLOCKS are supported. The original had no need for them; a book
    about a codebase is mostly quotation.

3.  render() is a FUNCTION over a string, not a module-level script over a fixed
    path. The original could only be tested by rewriting the real report; this can
    be asserted on three lines of synthetic markdown, which is how the block grammar
    below gets its tests.

WHAT WAS KEPT VERBATIM, AND WHY — both of these are scar tissue:

  * An unrecognised `<!-- ... -->` directive and an unhandled heading level both
    raise SystemExit. In the original, either one fell through every branch to a
    paragraph collector that could not consume it, so `i` never advanced and the
    build appended empty paragraphs until the machine died: 22 minutes, 4.3 GB, no
    error, no partial output. Twice, by two different doors.
  * End marks are applied BACK TO FRONT, because closing a section can insert a
    block and shift every index after it.
"""
from __future__ import annotations

import argparse
import html
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from press_css import CSS                     # noqa: E402
from press_economist import econ_css          # noqa: E402
import claims as _claims                      # noqa: E402
import plates as _plates                      # noqa: E402

NUMY = re.compile(r"^[\s$]*[\d(]|^[-–—]{1,2}$|%|×|x$")
ENDMARK = '<span class="endmark"></span>'
ABBREV = {"st.", "mr.", "ms.", "dr.", "inc.", "no.", "co.", "u.s."}


# ------------------------------------------------------------------ inline
def inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*]+?)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    # links run AFTER bold/em so a link inside a **…** span still renders; the
    # original shipped literal "[text](href)" to a paying reader by doing this first
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', t)
    t = t.replace("--", "&ndash;").replace("&amp;ndash;", "&ndash;")
    return t


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


# ------------------------------------------------------------------ grammar
def parse_blocks(lines: list[str]) -> list[tuple]:
    blocks, i = [], 0
    while i < len(lines):
        ln = lines[i]

        if ln.strip().startswith("```"):
            lang = ln.strip()[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1                                    # consume the closing fence
            blocks.append(("code", lang, "\n".join(buf)))
            continue

        if ln.strip().startswith("<!--"):
            raise SystemExit(
                f"press.py: unrecognised directive at line {i+1}: {ln.strip()!r}\n"
                "  this press defines none — remove it or add a branch here.")

        if (ln.startswith("|") and i + 1 < len(lines)
                and set(lines[i+1].replace("|", "").strip()) <= set("-: ")):
            head = cells(ln); i += 2; rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i])); i += 1
            blocks.append(("table", head, rows))
            continue

        if ln.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip()); i += 1
            # joined with newlines so a bare ">" survives as a paragraph break
            blocks.append(("quote", "\n".join(buf)))
            continue

        if ln.startswith("!! "):
            buf = [ln[3:].strip()]; i += 1
            while (i < len(lines) and lines[i].strip()
                   and not lines[i].startswith(("#", "|", ">", "- ", "!! ", "```"))):
                buf.append(lines[i].strip()); i += 1
            blocks.append(("pull", " ".join(buf)))
            continue

        if ln.startswith("#### "): blocks.append(("h4", ln[5:])); i += 1; continue
        if ln.startswith("### "):  blocks.append(("h3", ln[4:])); i += 1; continue
        if ln.startswith("## "):   blocks.append(("h2", ln[3:])); i += 1; continue
        if ln.startswith("#"):
            raise SystemExit(
                f"press.py: unhandled heading at line {i+1}: {ln!r}\n"
                "  only ##, ### and #### are rendered — fix the markdown "
                "or add a branch here.")

        if ln.startswith("- "):
            buf = []
            while i < len(lines) and (lines[i].startswith("- ")
                                      or (buf and lines[i].startswith("  ") and lines[i].strip())):
                if lines[i].startswith("- "):
                    buf.append(lines[i][2:].strip())
                else:
                    buf[-1] += " " + lines[i].strip()
                i += 1
            blocks.append(("ul", buf))
            continue

        if ln.strip() in ("", "---"):
            i += 1
            continue

        buf = []
        while (i < len(lines) and lines[i].strip()
               and not lines[i].startswith(("#", "|", ">", "- ", "<!--", "!! ", "```"))
               and lines[i].strip() != "---"):
            buf.append(lines[i].strip()); i += 1
        # Defensive: if the collector consumed nothing we would spin forever. The
        # original had exactly this hole and it cost 4.3GB twice.
        if not buf:
            raise SystemExit(f"press.py: cannot parse line {i+1}: {ln!r}")
        blocks.append(("p", " ".join(buf)))
    return blocks


# ------------------------------------------------------------------ furniture
def render_table(head, rows) -> str:
    nc = [any(NUMY.search(re.sub(r"[*<>a-z/ ]", "", r[j])) for r in rows if j < len(r))
          for j in range(len(head))]
    th = "".join(f'<th class="num">{inline(h)}</th>' if nc[j] and j > 0 else f"<th>{inline(h)}</th>"
                 for j, h in enumerate(head))
    labels = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inline(h)))).strip()
              for h in head]

    def cell(j, c):
        cls = ' class="num"' if (j < len(nc) and nc[j] and j > 0) else ""
        lbl = (f' data-th="{html.escape(labels[j], quote=True)}"'
               if 0 < j < len(labels) and labels[j] else "")
        return f'<td{cls}{lbl}><span class="tv">{inline(c)}</span></td>'

    tr = []
    for r in rows:
        hi = ' class="hi"' if any("**" in c for c in r[1:]) else ""
        tr.append(f"<tr{hi}>" + "".join(cell(j, c) for j, c in enumerate(r)) + "</tr>")
    return ('<div class="tw"><table><thead><tr>' + th + "</tr></thead><tbody>"
            + "".join(tr) + "</tbody></table></div>")


def small_caps_opener(el: str) -> str:
    m = re.match(r"(<p(?:\s[^>]*)?>)(.*)(</p>)$", el, re.S)
    if not m:
        return el
    tag, inner, close = m.groups()
    lead = re.match(r"[^<]+", inner)
    if not lead:
        return el
    words = lead.group(0).split(" ")
    take = []
    for i, w in enumerate(words):
        take.append(w)
        chars = len(" ".join(take))
        if w.endswith(".") and len(w) >= 4 and w.lower() not in ABBREV:
            break
        if w.endswith(",") and i >= 1 and chars >= 9:
            break
        if i >= 2 and chars >= 12:
            break
        if i >= 4:
            break
    while take and take[-1].lower() in ABBREV and len(take) < len(words):
        take.append(words[len(take)])
    head = " ".join(take)
    return f'{tag}<span class="sc">{head}</span>{inner[len(head):]}{close}'


def anchor_for(heading: str) -> str:
    return "s-" + re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:40]


# ------------------------------------------------------------------ render
def render(md: str, root: pathlib.Path, *, title: str, wordmark: str, eyebrow: str,
           dateline: str = "", standfirst_label: str = "", standfirst: str = "",
           promise: str = "") -> tuple[str, list[str], dict]:
    """Return (html, problems, stats). Problems are claim-gate failures."""
    n_claims = _claims.count_tokens(md)
    md, problems = _claims.resolve(md, root, esc=html.escape)
    blocks = parse_blocks(md.split("\n"))

    out: list[str] = []
    toc: list[tuple[str, str, str]] = []
    anchors: set[str] = set()
    lede_done = False

    for b in blocks:
        k = b[0]
        if k == "h2":
            t = b[1]
            m = re.match(r"^(\d\d)\s*·\s*(.*)$", t)
            a = anchor_for(t)
            anchors.add(a)
            pl = _plates.plate_for(a)
            if pl:
                out.append(pl)
            if m:
                toc.append((m.group(1), m.group(2), a))
                out.append(f'<h2 id="{a}"><span class="n">Section {m.group(1)}</span>'
                           f'<span class="ht">{inline(m.group(2))}</span></h2>')
            else:
                toc.append(("", t, a))
                out.append(f'<h2 id="{a}" class="h2-app"><span class="n">Appendix</span>'
                           f'<span class="ht">{inline(t)}</span></h2>')
        elif k == "h3":
            out.append(f"<h3>{inline(b[1])}</h3>")
        elif k == "h4":
            out.append(f"<h4>{inline(b[1])}</h4>")
        elif k == "pull":
            out.append(f'<p class="pull">{inline(b[1])}</p>')
        elif k == "p":
            if not lede_done:
                out.append(f'<p class="lede">{inline(b[1])}</p>'); lede_done = True
            else:
                out.append(f"<p>{inline(b[1])}</p>")
        elif k == "ul":
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in b[1]) + "</ul>")
        elif k == "code":
            lang = f' data-lang="{html.escape(b[1])}"' if b[1] else ""
            out.append(f'<pre class="src"{lang}><code>{html.escape(b[2])}</code></pre>')
        elif k == "quote":
            t = b[1]
            m = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", t, re.S)
            lbl, body = (m.group(1).strip().rstrip("."), m.group(2)) if m else ("Note", t)
            cls = "ednote" if lbl.lower().startswith(("editor", "note")) else "pending"
            paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()]
            out.append(f'<div class="{cls}"><p class="lbl">{html.escape(lbl) or "Note"}</p>'
                       + "".join(f"<p>{inline(p)}</p>" for p in paras) + "</div>")
        elif k == "table":
            out.append(render_table(b[1], b[2]))

    h2_at = [i for i, el in enumerate(out) if el.startswith("<h2")]
    for i in h2_at:
        if i + 1 < len(out) and out[i + 1].startswith("<p"):
            out[i + 1] = small_caps_opener(out[i + 1])

    # back to front: closing a section can INSERT a block and shift later indices
    for n in range(len(h2_at) - 1, -1, -1):
        i = h2_at[n]
        end = h2_at[n + 1] if n + 1 < len(h2_at) else len(out)
        while end > i + 1 and out[end - 1].startswith('<div class="plate"'):
            end -= 1
        if end <= i + 1:
            continue
        last = out[end - 1]
        if last.endswith("</p>") and 'class="pull"' not in last:
            out[end - 1] = last[: -len("</p>")] + ENDMARK + "</p>"
        else:
            out.insert(end, f'<p class="em-line">{ENDMARK}</p>')

    index = ('<nav class="index" aria-label="Contents"><ol>' + "".join(
        f'<li><span class="n">{n or "&middot;"}</span><a href="#{a}">{html.escape(t)}</a></li>'
        for n, t, a in toc) + "</ol></nav>")

    for orphan in _plates.orphaned_anchors(anchors):
        problems.append(f"plate manifest points at a section that does not exist: {orphan!r} "
                        "— a heading was retitled and its plate silently stopped rendering")

    head_bits = [f'<div class="wordmark">{html.escape(wordmark)}</div>']
    if eyebrow:
        head_bits.append(f'<p class="eyebrow">{inline(eyebrow)}</p>')
    mast = "<header class=\"mast\">" + "".join(head_bits) + "</header>"

    front = [mast, _plates.hero(), f"<h1>{inline(title)}</h1>"]
    if dateline:
        front.append(f'<p class="dateline">{dateline}</p>')
    if standfirst:
        front.append('<div class="premise">'
                     + (f'<p class="lbl">{html.escape(standfirst_label)}</p>' if standfirst_label else "")
                     + f"<p>{inline(standfirst)}</p></div>")
    front.append(index)

    tail = f'<p class="promise">{inline(promise)}</p>' if promise else ""

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{CSS}
{econ_css()}
<style>
  pre.src {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
             font-size: 12.5px; line-height: 1.5; background: var(--wash, #F1F0EA);
             border-left: 3px solid var(--rule); padding: 12px 14px; overflow-x: auto;
             max-width: var(--measure); margin: 20px 0; white-space: pre; }}
  pre.src code {{ background: none; padding: 0; font-size: inherit; }}
  .claim-broken {{ background: var(--hot); color: #fff; padding: 1px 5px; font-weight: 700; }}
  @media print {{ pre.src {{ font-size: 8pt; break-inside: avoid; }} }}
</style>
</head>
<body>
<div class="sheet">
{''.join(front)}
{chr(10).join(out)}
{tail}
</div>
</body>
</html>
"""
    stats = {
        "sections": len(toc),
        "tables": doc.count("<table"),
        "pulls": doc.count('class="pull"'),
        "code_blocks": doc.count('<pre class="src"'),
        "claims": n_claims,
        "plates": doc.count('<div class="plate'),
        "missing_plates": _plates.missing_plates(),
        "chars": len(doc),
    }
    return doc, problems, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a markdown source into the press format.")
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--wordmark", default="Research")
    ap.add_argument("--eyebrow", default="")
    ap.add_argument("--dateline", default="")
    ap.add_argument("--standfirst-label", default="")
    ap.add_argument("--standfirst", default="")
    ap.add_argument("--promise", default="")
    ap.add_argument("--repo-root", default=None,
                    help="claim tokens resolve against this (default: the git root)")
    a = ap.parse_args()

    root = pathlib.Path(a.repo_root) if a.repo_root else HERE.parents[2]
    src = pathlib.Path(a.source).read_text()
    doc, problems, stats = render(
        src, root, title=a.title, wordmark=a.wordmark, eyebrow=a.eyebrow,
        dateline=a.dateline, standfirst_label=a.standfirst_label,
        standfirst=a.standfirst, promise=a.promise)

    out = pathlib.Path(a.out)
    out.write_text(doc)
    print(f"wrote {out} — {stats['chars']:,} chars · {stats['sections']} sections · "
          f"{stats['tables']} tables · {stats['code_blocks']} code blocks · "
          f"{stats['pulls']} pull statements · {stats['plates']} plates · "
          f"{stats['claims']} claims")
    if stats["missing_plates"]:
        print(f"  plates not yet delivered: {', '.join(stats['missing_plates'])}")
    if problems:
        print("\nCLAIM GATE FAILED — a claim about this repo no longer traces:")
        for p in problems:
            print("  " + p)
        return 1
    print("claim gate: every claim traces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
