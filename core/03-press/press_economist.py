"""The magazine layer — the report's Economist edition.

Layered ON TOP of report_css.CSS rather than folded into it, deliberately:
the base is the design system the teaser, preview and access hub also read
from, and this is the report's own editorial treatment. Keeping it a
separate stylesheet means the report can carry it without dragging four
other surfaces along, and means it can be lifted back off in one line.

Typefaces are embedded from build/fonts/ so the report stays one
self-contained artifact — it is served by reading a single file off disk,
and reviewed as a local file:// copy.
"""
import base64
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
FONT_DIR = HERE / "fonts"

# (family, style, weight, stretch, file)
_FACES = [
    ("Archivo", "normal", "400 800", "75% 100%", "archivo-var.woff2"),
    ("Source Serif 4", "normal", "400 700", None, "ss4-roman.woff2"),
    ("Source Serif 4", "italic", "400 600", None, "ss4-italic.woff2"),
]


def font_faces() -> str:
    """The three embedded faces. PUBLIC because the free funnel surfaces read
    it too (`funnel_typeface.py`) — one definition of what "the report's type"
    is, so the teaser cannot end up on a different cut of Source Serif."""
    out = ["  /* Archivo and Source Serif 4, both SIL OFL, embedded so the",
           "     report renders identically offline and on any machine. */"]
    for family, style, weight, stretch, filename in _FACES:
        path = FONT_DIR / filename
        if not path.exists():
            raise SystemExit(f"font missing: {path}")
        b64 = base64.b64encode(path.read_bytes()).decode()
        stretch_decl = f"font-stretch:{stretch};" if stretch else ""
        out.append(
            f'  @font-face{{font-family:"{family}";font-style:{style};'
            f"font-weight:{weight};{stretch_decl}font-display:block;"
            f'src:url(data:font/woff2;base64,{b64}) format("woff2");}}'
        )
    return "\n".join(out)


_LAYER = r"""
  /* ==================================================================
     THE ECONOMIST EDITION — magazine restyle layer
     ==================================================================

     What makes a page read as The Economist is not its red. It is a
     two-font system used with discipline:

       · a condensed grotesque for EVERY piece of furniture — headline,
         kicker, chart title, axis tick, table figure;
       · a sturdy low-contrast serif for running text, and nothing else.

     So this layer retires the mono register (--mono now resolves to the
     grotesque) and the serif display type the previous restyle used.
     Headlines go sans, body stays serif — the newsweekly arrangement,
     the exact inverse of what was here before.

     The palette is NOT swapped to Economist red (#E3120B). Twenty
     commissioned linocuts are printed in this document's rust/teal/
     ochre, and a second red would fight every one of them. Instead the
     existing rust plays the role Economist red plays: masthead block,
     kicker, chart tab, end-of-article square, and nothing else.

     Typefaces are embedded, not linked, so the file stays one artifact
     that renders identically offline and on any machine:
       Archivo (SIL OFL) — grotesque, width axis 75-100%, for EconSans
       Source Serif 4 (SIL OFL) — text serif, for Milo Serif
     ================================================================== */

  :root {
    --sans:  "Archivo", "Liberation Sans Narrow", "Helvetica Neue", Arial, sans-serif;
    --serif: "Source Serif 4", Charter, Georgia, "Times New Roman", serif;
    /* the mono register is retired: one grotesque now carries every
       kicker, tick, table figure and caption in the document */
    --mono:  "Archivo", "Liberation Sans Narrow", "Helvetica Neue", Arial, sans-serif;

    --wordmark-ink: #FFFFFF;
    --wash: #F1F0EA;          /* tinted panel for asides, warm not gray */
    --measure: 730px;
    /* One column, one right edge. Running text used to stop 105px short of
       the figures and tables it sits between, which reads as a ragged right
       margin rather than as a deliberately narrower measure. Prose now
       fills the same column as everything else, and the type size goes up
       so the line still lands near 75 characters at that width. */
    --prose: var(--measure);
  }
  @media (prefers-color-scheme: dark) {
    :root { --wordmark-ink: #16100D; --wash: #1B2027; }
  }
  :root[data-theme="dark"] { --wordmark-ink: #16100D; --wash: #1B2027; }
  :root[data-theme="light"] { --wordmark-ink: #FFFFFF; --wash: #F1F0EA; }

  /* ---- running text ------------------------------------------------ */
  body {
    font-family: var(--serif);
    font-size: 19.5px; line-height: 1.6;
    font-optical-sizing: auto;
    padding-bottom: 72px;
  }
  /* the sheet IS the measure, so every rule, plate and headline shares one
     centered column instead of hanging left inside a wider sheet */
  .sheet { max-width: var(--measure); }
  p { margin: 0 0 14px; }

  /* Running text is justified and hyphenated, the way the printed
     magazine sets it. Everything that is furniture rather than argument
     — captions, kickers, card copy, the appendix — stays ragged, where
     justification would only open gaps. */
  p, li { text-align: justify; hyphens: auto; -webkit-hyphens: auto; orphans: 2; widows: 2; }
  /* only p/li ever justified, so this opt-out list is p/li too — touching
     th/td here would undo the right-aligned numeric columns */
  p.pull, p.dateline, p.promise, p.eyebrow, .premise p, p.qnote,
  p.bp-ops, p.bp-tot, p.bp-where, p.bp-none, h2.h2-app ~ p {
    text-align: left; hyphens: manual; -webkit-hyphens: manual;
  }
  strong { font-weight: 650; }
  em { font-style: italic; }
  a { border-bottom-width: 1px; }

  /* every numeral in the document lines up in a column */
  table, .qbig, .bp-sub, .bp-n, .tick, .val-label, .qstats, .dateline {
    font-variant-numeric: tabular-nums;
  }

  /* ---- the strip above the masthead -------------------------------- */
  .topbar {
    font-family: var(--sans); font-size: 11.5px; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3);
    padding: 13px 0 12px; margin-bottom: 0;
    border-bottom: 1px solid var(--rule-soft);
  }
  .topbar a { color: var(--hot); border-bottom: 0; }
  .topbar a:hover { border-bottom: 1px solid currentColor; }

  /* ---- masthead: the red block ------------------------------------- */
  .mast { border-bottom: 0; padding: 22px 0 0; }
  .wordmark {
    display: inline-block; background: var(--hot); color: var(--wordmark-ink);
    font-family: var(--sans); font-weight: 700; font-stretch: 92%;
    font-size: 20px; letter-spacing: .015em; line-height: 1;
    padding: 11px 17px 12px;
  }
  .mast .eyebrow {
    font-family: var(--sans); font-size: 11.5px; font-weight: 600;
    letter-spacing: .13em; text-transform: uppercase; color: var(--ink-2);
    margin: 14px 0 0; padding-bottom: 12px;
    border-bottom: 2px solid var(--ink); max-width: var(--measure);
  }

  /* ---- cover ------------------------------------------------------- */
  .plate.hero { margin: 20px 0 24px; }
  .plate.hero img { height: auto; }   /* whole image; see .plate img above */

  h1 {
    font-family: var(--sans); font-weight: 800; font-stretch: 84%;
    font-size: clamp(36px, 5.6vw, 58px); line-height: .99;
    /* the width axis narrows the word space along with everything else,
       so give it back what the condensation took */
    letter-spacing: -0.021em; word-spacing: .035em; margin: 0 0 0;
    max-width: var(--measure); text-wrap: balance;
  }
  /* Sized up 12px -> 13px on 2026-08-07 (operator ask). 13 is the ceiling that
     still holds ONE row against the 730px measure at .03em; 14px wrapped. The
     funnel's copy of this line caps lower (12.5px) because the teaser's measure
     is 720px — the two surfaces are deliberately NOT identical here. Measured
     in Chrome at 1280px and 1024px, not reasoned about. */
  .dateline {
    font-family: var(--sans); font-size: 13px; font-weight: 700;
    letter-spacing: .03em; text-transform: uppercase; color: var(--hot);
    margin: 18px 0 0;
  }

  /* The standfirst: italic serif under the headline, the way a
     newsweekly sets the sentence that tells you why to read on. */
  .premise {
    border-left: 0; padding: 0; margin: 14px 0 0;
    max-width: var(--prose);
  }
  .premise .lbl {
    font-family: var(--sans); font-size: 10px; font-weight: 700;
    letter-spacing: .15em; text-transform: uppercase; color: var(--ink-3);
    margin: 0 0 7px;
  }
  .premise p {
    font-family: var(--serif); font-style: italic; font-weight: 400;
    font-size: 22px; line-height: 1.36; letter-spacing: 0;
    color: var(--ink-2);
  }

  /* ---- contents ---------------------------------------------------- */
  .index {
    border-top: 1px solid var(--ink); border-bottom: 1px solid var(--ink);
    padding: 16px 0 18px; margin: 34px 0 8px; max-width: var(--measure);
  }
  .index::before {
    content: "Contents"; display: block;
    font-family: var(--sans); font-size: 10px; font-weight: 700;
    letter-spacing: .16em; text-transform: uppercase; color: var(--ink-3);
    margin-bottom: 12px;
  }
  .index ol { gap: 8px; }
  .index li { grid-template-columns: 34px 1fr; gap: 10px; }
  .index .n {
    font-family: var(--sans); font-size: 11px; font-weight: 700;
    letter-spacing: .06em; color: var(--hot); padding-top: 3px;
  }
  .index a {
    font-family: var(--sans); font-size: 15.5px; font-weight: 600;
    font-stretch: 95%; letter-spacing: -0.004em; border-bottom: 0;
  }
  .index a:hover, .index a:focus-visible { border-bottom: 0; color: var(--hot); }

  /* ---- section opener: plate, rule, kicker, headline ---------------- */
  /* The plate and the heading under it are ONE unit — a chapter opener.
     Big air above the plate, almost none between plate and headline. */
  .plate { width: var(--measure); max-width: 100%; margin: 62px 0 0; }
  /* NO aspect-ratio, NO object-fit:cover. The original press cropped every plate to a
     30:9 letterbox because its illustrations were COMMISSIONED at that ratio and cropped
     before they ever reached the build. Feed it a square image and cover() throws away
     70% of it — silently, because a cropped image still looks like a deliberate one.
     Natural ratio instead: whatever the illustration is, the reader sees all of it. */
  .plate img {
    display: block; width: 100%; height: auto;
    background: var(--panel);
  }
  .plate + h2 { margin-top: 16px !important; }

  h2 {
    display: block; border-bottom: 0; padding-bottom: 0;
    margin: 52px 0 18px; max-width: var(--measure);
  }
  h2 .n {
    display: block; font-family: var(--sans);
    font-size: 11.5px; font-weight: 700; letter-spacing: .15em;
    text-transform: uppercase; color: var(--hot);
    border-top: 2px solid var(--ink); padding: 10px 0 0; margin: 0 0 11px;
  }
  h2 .ht {
    display: block; font-family: var(--sans); font-weight: 700;
    font-stretch: 86%; font-size: clamp(28px, 3.9vw, 42px);
    line-height: 1.02; letter-spacing: -0.019em; word-spacing: .035em;
    text-wrap: balance; color: var(--ink);
  }
  /* The appendix is not a chapter. It is set a step down, and its prose
     drops to the grotesque at source-note size — the way a newsweekly
     sets methodology, so it reads as apparatus rather than argument. */
  h2.h2-app .ht { font-size: clamp(23px, 2.6vw, 29px); }
  h2.h2-app ~ p:not(.promise) {
    font-family: var(--sans); font-size: 13.5px; line-height: 1.62;
    color: var(--ink-2); max-width: var(--measure);
  }

  h3 {
    font-family: var(--sans); font-weight: 700; font-stretch: 94%;
    font-size: 20px; line-height: 1.2; letter-spacing: -0.012em;
    margin: 34px 0 10px; max-width: var(--measure); text-wrap: balance;
  }

  /* ---- the small-caps opener --------------------------------------- */
  /* No drop cap. The Economist opens an article by setting the first
     few words in small capitals, which is quieter and lets the plate
     above stay the loudest thing on the page. */
  .sc {
    font-variant-caps: normal; text-transform: uppercase;
    font-size: .84em; letter-spacing: .045em; font-weight: 600;
  }
  .lede::first-letter, h2 + p::first-letter {
    float: none; font-size: inherit; line-height: inherit;
    margin: 0; padding: 0; color: inherit; font-family: inherit;
    font-weight: inherit;
  }

  /* end-of-article square */
  .endmark {
    display: inline-block; width: .5em; height: .5em;
    background: var(--hot); margin-left: .45em;
  }
  /* a section that closes on a list gets the square on its own line */
  .em-line { margin: -2px 0 0; max-width: var(--measure); }

  /* ---- pull quotes ------------------------------------------------- */
  .pull {
    font-family: var(--sans); font-weight: 600; font-stretch: 92%;
    font-size: 23px; line-height: 1.24; letter-spacing: -0.016em;
    border-left: 0; padding: 15px 0 0; margin: 26px 0 24px;
    max-width: var(--prose); position: relative;
  }
  .pull::before {
    content: ""; position: absolute; top: 0; left: 0;
    width: 44px; height: 4px; background: var(--hot);
  }
  .pull strong { font-weight: 600; }

  /* ---- asides ------------------------------------------------------ */
  .pending, .ednote {
    background: var(--wash); border: 0; border-top: 3px solid var(--cool);
    padding: 15px 18px 16px; max-width: var(--prose); font-size: 16px;
    line-height: 1.5;
  }
  .ednote { border-top-color: var(--hot); border-bottom: 0; }
  .pending .lbl, .ednote .lbl {
    font-family: var(--sans); font-size: 10.5px; font-weight: 700;
    letter-spacing: .13em; text-transform: uppercase;
  }

  ul { padding-left: 20px; }
  ul li { margin-bottom: 8px; }

  /* ---- figures: the Economist chart block --------------------------- */
  /* Red tab, title, note under the rule, and no frame anywhere. */
  figure.fig { margin: 34px 0 30px; max-width: var(--measure); }
  .fig-head {
    border-bottom: 0; padding-bottom: 0; margin-bottom: 14px;
    position: relative; padding-top: 15px;
  }
  .fig-head::before {
    content: ""; position: absolute; top: 0; left: 0;
    width: 30px; height: 5px; background: var(--hot);
  }
  .fig-n {
    font-family: var(--sans); font-size: 10px; font-weight: 700;
    letter-spacing: .15em; text-transform: uppercase; color: var(--ink-3);
    margin-bottom: 4px;
  }
  .fig-title {
    font-family: var(--sans); font-weight: 700; font-stretch: 94%;
    font-size: 20px; line-height: 1.18; letter-spacing: -0.013em;
    margin-top: 0; color: var(--ink); text-wrap: balance;
  }
  figcaption {
    font-family: var(--sans); font-size: 12.5px; font-weight: 400;
    line-height: 1.48; color: var(--ink-3); max-width: var(--measure);
    margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--rule-soft);
  }
  /* a recap is a callback, so it is set quieter — gray tab, no rule */
  .fig-recap { border-left: 0; padding-left: 0; }
  .fig-recap .fig-head::before { background: var(--rule); }

  /* every mark of type inside a chart is the grotesque */
  svg text { font-family: var(--sans); }
  .tick { font-family: var(--sans); font-size: 10.5px; font-weight: 500; fill: var(--ink-3); }
  .val-label { font-family: var(--sans); font-size: 10.5px; font-weight: 700; fill: var(--ink-2); }
  .series-label { font-family: var(--sans); font-size: 11.5px; font-weight: 700; font-stretch: 92%; }
  .strap { font-family: var(--sans); font-weight: 700; font-stretch: 92%; opacity: .8; }
  .grid-line { stroke: var(--rule-soft); }
  .axis-line { stroke: var(--ink-3); }

  /* charts pulled up to the plates' saturation. report_figs.py emits
     fill-opacity as SVG presentation attributes and any CSS rule
     outranks those, so the charts restyle without touching the
     generator. */
  .fig svg rect[fill^="var(--hot"], .fig svg rect[fill="var(--hot)"] { fill-opacity: .88 !important; }
  .fig svg rect[fill="var(--cool)"]  { fill-opacity: .84 !important; }
  .fig svg rect[fill="var(--ochre)"] { fill-opacity: .88 !important; }
  /* ...EXCEPT where the opacity IS the measure. Figure 16 nests two series in
     one bar: the pale rect is every envelope a buyer submitted (capital at
     risk) and the solid rect inside it is the winning ones (money spent). The
     contrast between them is the finding, not styling. The blanket rules above
     flattened BOTH to .88, so from the restyle until 2026-08-07 that chart
     rendered as one solid bar and the operator read it as broken — correctly.
     Specificity (0,3,2) beats those rules' (0,2,2), so this survives being
     reordered, unlike an !important that only wins by coming later.
     Guarded by a contrast regression test which
     resolves the cascade rather than reading the presentation attribute. */
  .fig svg rect.atrisk[fill] { fill-opacity: .13 !important; }
  .fig svg rect.spent[fill]  { fill-opacity: .92 !important; }
  /* ...EXCEPT where the opacity IS the measure. Figure 16 nests two series in
     one bar: the pale rect is every envelope a buyer submitted (capital at
     risk) and the solid rect inside it is the winning ones (money spent). The
     contrast between them is the finding, not styling. The blanket rules above
     flattened BOTH to .88, so from the restyle until 2026-08-07 that chart
     rendered as one solid bar and the operator read it as broken — correctly.
     Specificity (0,3,2) beats those rules' (0,2,2), so this survives being
     reordered, unlike an !important that only wins by coming later. */
  .fig svg rect[stroke] { stroke-opacity: 1 !important; stroke-width: 1.2 !important; }
  .fig svg circle[fill="var(--hot)"], .fig svg circle[fill="var(--cool)"] { fill-opacity: 1 !important; }
  .fig svg polyline[stroke] { stroke-width: 2.8 !important; stroke-opacity: 1 !important; }
  .fig svg line[stroke="var(--ochre)"] { stroke-opacity: 1 !important; stroke-width: 2.4 !important; }
  .fig svg path[fill^="var(--"] { fill-opacity: .74 !important; }
  .fig svg text[fill="var(--panel)"] { fill: #FFFFFF !important; }

  .legend span {
    font-family: var(--sans); font-size: 10.5px; font-weight: 600;
    letter-spacing: .09em; color: var(--ink-2);
  }
  .swatch { border-radius: 0; width: 12px; height: 12px; }

  /* ---- tables: same block furniture as a chart ---------------------- */
  .tw { max-width: var(--measure); margin: 30px 0 26px; padding-top: 15px; position: relative; }
  .tw::before {
    content: ""; position: absolute; top: 0; left: 0;
    width: 30px; height: 5px; background: var(--hot);
  }
  table {
    font-family: var(--sans); font-size: 13.5px; font-weight: 400;
    font-variant-numeric: tabular-nums;
  }
  caption {
    font-family: var(--sans); font-size: 10px; font-weight: 700;
    letter-spacing: .15em; text-transform: uppercase; color: var(--ink-3);
    padding-bottom: 9px;
  }
  thead th {
    font-family: var(--sans); font-size: 11px; font-weight: 700;
    letter-spacing: .05em; color: var(--ink-2);
    border-bottom: 1.5px solid var(--ink); padding-bottom: 7px;
  }
  th, td { padding: 7px 12px 7px 0; }
  tbody tr:last-child td { border-bottom: 1.5px solid var(--ink); }
  tr.hi td { background: var(--hot-wash); }
  td strong, th strong { font-weight: 700; }

  /* ---- §05 buyer breakouts ----------------------------------------- */
  .bp {
    border: 0; border-top: 2px solid var(--ink); background: transparent;
    padding: 14px 0 4px; margin: 34px 0;
  }
  .bp-head { border-bottom: 1px solid var(--rule); padding-bottom: 9px; margin-bottom: 12px; }
  .bp h4 {
    font-family: var(--sans); font-weight: 700; font-stretch: 92%;
    font-size: 21px; letter-spacing: -0.014em;
  }
  .bp-sub, .bp-where, .bp-tot, .bp-ops, .bp-none {
    font-family: var(--sans); font-size: 13px;
  }
  .bp-sub { font-size: 12px; font-weight: 600; color: var(--ink-3); }
  table.bp-t { font-family: var(--sans); font-size: 12.5px; }
  table.bp-t th { font-family: var(--sans); font-size: 10px; letter-spacing: .07em; }

  /* ---- the four-group quadrant explainer ---------------------------- */
  .qcard {
    border: 0; background: var(--wash); padding: 16px 17px 15px;
    border-top: 3px solid var(--rule);
  }
  /* the card's top rule takes the color of its own eyebrow. The generator
     sets that color inline, so match on the inline value rather than on
     child position — the grid also holds the two .qhead labels, and
     counting children put a rust rule over a teal card. */
  .qcard:has(.qeyebrow[style*="cool"]) { border-top-color: var(--cool); }
  .qcard:has(.qeyebrow[style*="hot"])  { border-top-color: var(--hot); }
  .qhead, .qaxis, .qeyebrow, .qstats {
    font-family: var(--sans); letter-spacing: .1em;
  }
  .qbig {
    font-family: var(--sans); font-weight: 700; font-stretch: 88%;
    font-size: 34px; letter-spacing: -0.024em;
  }
  .qsub { font-family: var(--sans); font-size: 13px; }
  .qnote { font-family: var(--serif); font-size: 14.5px; line-height: 1.45; }
  .qlad, .qlad th, .qlad td, .qlad caption { font-family: var(--sans); }

  /* ---- close ------------------------------------------------------- */
  .method {
    font-family: var(--sans); font-size: 12px; line-height: 1.6;
    border-top: 2px solid var(--ink); margin-top: 48px; padding-top: 16px;
  }
  .promise {
    font-family: var(--serif); font-style: italic; font-size: 14px;
    color: var(--ink-3); margin-top: 18px;
    border-top: 1px solid var(--rule-soft); padding-top: 16px;
    max-width: var(--measure);
  }

  #tip { font-family: var(--sans); font-size: 11.5px; font-weight: 600; border-radius: 0; }

  /* ---- dark ---------------------------------------------------------
     The plates are printed on cream. On a dark page they are tipped-in
     prints, so they get an edge rather than pretending to be part of
     the ground. */
  @media (prefers-color-scheme: dark) {
    .plate img { border: 1px solid var(--rule); }
    .fig svg text[fill="var(--panel)"] { fill: #101319 !important; }
  }
  :root[data-theme="dark"] .plate img { border: 1px solid var(--rule); }

  /* Justification needs a measure. Below roughly 60 characters to the
     line it stops being a magazine and starts being word-gaps, so the
     phone reads ragged-right. */
  @media (max-width: 720px) {
    p, li { text-align: left; }
  }
  @media (max-width: 640px) {
    .plate img { height: auto; }
    .plate { margin-top: 42px; }
    body { font-size: 17px; }
    h2 .ht { font-size: 30px; }
  }

  /* ---- the phone ----------------------------------------------------
     This document is mostly tables, and a phone is narrower than every
     one of them. Left alone they slice off mid-word with no sign that
     they scroll, which reads as a broken page rather than as a wide one.
     Two different answers, because the tables are two different shapes. */

  /* The 232px label floor exists so one long row label in §02 holds a
     line at desktop width. On a narrow screen it is the ONLY reason most
     of these tables overflow at all. */
  @media (max-width: 760px) {
    th:first-child, td:first-child { min-width: 0; }
    table { min-width: 0; }
  }

  /* The argument tables — 2 to 6 columns — stop being tables and become
     one labeled block per row. Every cell already carries its column
     heading in data-th, so nothing is lost in the fold. */
  @media (max-width: 620px) {
    .tw { overflow-x: visible; }
    .tw table, .tw tbody, .tw tr, .tw td { display: block; width: 100%; box-sizing: border-box; }
    .tw thead { display: none; }
    .tw tr {
      padding: 11px 0 12px; border-bottom: 1px solid var(--rule-soft);
    }
    .tw tbody tr:last-child { border-bottom: 1.5px solid var(--ink); }
    /* the wash belongs to the row here, not to each cell — both would
       double up and leave paler bands in the gaps between them */
    .tw tr.hi { background: var(--hot-wash); padding-inline: 11px; }
    .tw tr.hi td { background: none; }
    /* Grid, not flex, and the difference matters: a cell like
       "<strong>one year</strong> — RSMo 140.340" is several inline nodes.
       Flex makes each one its own item and space-between then scatters
       them across the row. Grid wraps a contiguous run of inline content
       in ONE anonymous item, so the value stays a single block. */
    .tw td {
      border: 0; padding: 3px 0; white-space: normal;
      display: grid; grid-template-columns: minmax(0, 44%) minmax(0, 1fr);
      gap: 4px 16px; align-items: baseline; text-align: right;
    }
    .tw td::before {
      content: attr(data-th); text-align: left;
      color: var(--ink-3); font-weight: 600; font-size: .88em;
      letter-spacing: .01em; line-height: 1.3;
    }
    /* the row's own label leads the block, so it is a heading, not a pair */
    .tw td:first-child {
      display: block; text-align: left; font-weight: 700; font-size: 1.06em;
      padding-bottom: 5px; color: var(--ink);
    }
    .tw td:first-child::before { content: none; }
    .tw td:first-child:empty { display: none; }
    .tw tbody tr:last-child td { border-bottom: 0; }
  }

  /* The §05 buyer breakouts are eleven columns wide and five rows deep.
     Folding those would be 55 lines a table, so they keep scrolling —
     but they say so, and the edge shows there is more. */
  .bp-scroll, .fig-scroll { position: relative; }
  @media (max-width: 620px) {
    .bp-scroll::after, .fig-scroll::after {
      content: ""; position: absolute; top: 0; right: 0; bottom: 0; width: 34px;
      background: linear-gradient(to left, var(--ground), transparent);
      pointer-events: none;
    }
    .bp-scroll::before {
      content: "swipe for the rest \2192"; display: block;
      font-family: var(--sans); font-size: 10px; font-weight: 700;
      letter-spacing: .12em; text-transform: uppercase; color: var(--ink-3);
      padding-bottom: 6px;
    }
    .bp-t td, .bp-t th { padding-inline: 6px; }
  }

  /* ---- print -------------------------------------------------------- */
  @media print {
    /* Print gets the same single column. At the full 178mm printable width
       one column of 9.8pt runs past 100 characters, so the column narrows
       and the type grows instead — the sheet centers itself in what is
       left, which is where the wider page margins come from. */
    :root { --measure: 156mm; --prose: var(--measure); --wash: #F4F3EF; --wordmark-ink: #fff; }
    body { font-size: 11pt; line-height: 1.46; }
    .topbar { display: none; }
    .mast { padding: 0; }
    .wordmark { font-size: 13pt; padding: 7px 12px 8px; }
    .mast .eyebrow { font-size: 7.6pt; margin-top: 9px; padding-bottom: 8px; }
    h1 { font-size: 30pt; }
    .dateline { font-size: 8pt; margin-top: 11px; }
    .premise p { font-size: 13pt; }
    .index { margin: 18px 0 4px; }
    .index a { font-size: 9.6pt; }
    .plate { margin-top: 20px; break-inside: avoid; page-break-inside: avoid; }
    /* A 1:1 plate at the full print measure is 156mm tall on a 216mm printable page,
       which leaves no room for the headline it belongs to. Bound the HEIGHT and let the
       width fall out of it, so the whole illustration is still shown — just smaller.
       Capping the width instead would crop again, which is the bug this is fixing. */
    .plate img { width: auto; max-width: 100%; max-height: 108mm; margin: 0 auto; }
    .plate.hero img { max-height: 96mm; }
    h2 { margin: 20px 0 10px; break-after: avoid; }
    h2 .n { font-size: 8pt; padding-top: 7px; margin-bottom: 7px; }
    h2 .ht { font-size: 20pt; }
    h2.h2-app .ht { font-size: 15pt; }
    h3 { font-size: 12pt; margin: 18px 0 6px; break-after: avoid; }
    p { margin: 0 0 8px; }
    .pull { font-size: 13pt; margin: 14px 0 13px; padding-top: 11px; break-inside: avoid; }
    .pull::before { width: 34px; height: 3px; }
    figure.fig { margin: 16px 0 14px; break-inside: avoid; }
    .fig-head { padding-top: 11px; margin-bottom: 9px; }
    .fig-head::before { width: 22px; height: 4px; }
    .fig-title { font-size: 12pt; }
    .fig-n { font-size: 7pt; }
    figcaption { font-size: 8pt; margin-top: 9px; padding-top: 7px; line-height: 1.36; }
    .tw { margin: 14px 0 14px; padding-top: 11px; break-inside: avoid; }
    .tw::before { width: 22px; height: 4px; }
    table { font-size: 8.4pt; }
    thead th { font-size: 7.4pt; }
    th, td { padding: 4px 9px 4px 0; }
    .pending, .ednote { padding: 10px 13px; break-inside: avoid; }
    .bp { break-inside: avoid; }
    .bp h4 { font-size: 13pt; }
    .qbig { font-size: 19pt; }
    .method { font-size: 7.6pt; margin-top: 22px; }
    .promise { font-size: 9pt; }
    .tick, .val-label { font-size: 9px; }
  }
"""


def econ_css() -> str:
    """The <style> block: embedded faces, then the layer."""
    return "<style>\n" + font_faces() + "\n" + _LAYER + "\n</style>"
