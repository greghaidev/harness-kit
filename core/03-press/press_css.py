CSS = """<style>
  /* ============================================================
     The base design system for the press.

     Design system inherited from teaser.html so the teaser, the
     preview and the guide read as one publication: same tokens,
     same 820px sheet over a 720px measure, same sans/serif/mono
     roles, same county-bid-sheet table register, same figure
     furniture. Palette validated (6 checks, light + dark)
     2026-07-19. Scaled up only where a long document needs more
     hierarchy than a two-page teaser does.
     ============================================================ */

  :root {
    --ground:      #FAFAF8;
    --panel:       #FFFFFF;
    --ink:         #14171C;
    --ink-2:       #3D444F;
    --ink-3:       #6B7280;
    --rule:        #D8DAD5;
    --rule-soft:   #E9EAE6;
    --hot:         #B8391A;
    --cool:        #0091B0;
    /* --cool is a graphic color: 3.69:1 on white, fine for a 2px line and
       for 30px display type, under the 4.5:1 floor for small text */
    --cool-ink:    #006E86;
    --ochre:       #8E7B12;
    --hot-wash:    rgba(184, 57, 26, 0.09);
    --cool-wash:   rgba(0, 145, 176, 0.09);

    --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
    --measure: 720px;   /* one shared right edge for figures, tables and display
                           type, matching the printed sheet's usable width */
    --prose: 610px;     /* running text stops short of it: at 720px this document
                           ran 96 characters to the line, well past the 65-75 a
                           reader tracks comfortably. Figures still get the 720. */
  }

  @media (prefers-color-scheme: dark) {
    :root {
      --ground: #101319; --panel: #161A21; --ink: #EEF0F3; --ink-2: #B4BAC4;
      --ink-3: #858D99; --rule: #2C323C; --rule-soft: #222831;
      --hot: #E4693F; --cool: #35B4D2; --cool-ink: #52C3DE; --ochre: #BCA430;
      --hot-wash: rgba(228,105,63,.12); --cool-wash: rgba(53,180,210,.12);
    }
  }
  :root[data-theme="dark"] {
    --ground: #101319; --panel: #161A21; --ink: #EEF0F3; --ink-2: #B4BAC4;
    --ink-3: #858D99; --rule: #2C323C; --rule-soft: #222831;
    --hot: #E4693F; --cool: #35B4D2; --cool-ink: #52C3DE; --ochre: #BCA430;
    --hot-wash: rgba(228,105,63,.12); --cool-wash: rgba(53,180,210,.12);
  }
  :root[data-theme="light"] {
    --ground: #FAFAF8; --panel: #FFFFFF; --ink: #14171C; --ink-2: #3D444F;
    --ink-3: #6B7280; --rule: #D8DAD5; --rule-soft: #E9EAE6;
    --hot: #B8391A; --cool: #0091B0; --cool-ink: #006E86; --ochre: #8E7B12;
    --hot-wash: rgba(184,57,26,.09); --cool-wash: rgba(0,145,176,.09);
  }

  body {
    background: var(--ground); color: var(--ink);
    font-family: var(--serif); font-size: 16.5px; line-height: 1.6;
    margin: 0; padding: 0 20px 56px; -webkit-font-smoothing: antialiased;
  }
  .sheet { max-width: 820px; margin: 0 auto; }
  p, li { max-width: var(--prose); text-wrap: pretty; }

  /* ---- masthead ------------------------------------------------ */
  .mast { border-bottom: 2px solid var(--ink); padding: 30px 0 10px; }
  .eyebrow {
    font-family: var(--mono); font-size: 10.5px; font-weight: 600;
    letter-spacing: .13em; text-transform: uppercase; color: var(--ink-2);
    margin: 0;
  }
  h1 {
    font-family: var(--sans);
    font-size: clamp(29px, 4.6vw, 42px); line-height: 1.06;
    letter-spacing: -0.023em; font-weight: 700;
    max-width: var(--measure); text-wrap: pretty; margin: 22px 0 0;
  }
  .dateline {
    font-family: var(--mono); font-size: 11px; font-weight: 600;
    letter-spacing: .1em; text-transform: uppercase; color: var(--ink-2);
    margin: 16px 0 4px; max-width: var(--measure);
  }
  /* The deadline is the one date a bidder cannot recover from missing, so it
     carries the accent while the opening date stays in the quieter ink. */
  .dateline strong { color: var(--hot); font-weight: 700; }

  /* ---- premise -------------------------------------------------- */
  .premise {
    border-left: 3px solid var(--hot); padding: 4px 0 4px 15px;
    margin: 26px 0 0; max-width: var(--measure); box-sizing: border-box;
  }
  .premise .lbl {
    font-family: var(--mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: .11em; text-transform: uppercase; color: var(--hot);
    display: block; margin: 0 0 6px;
  }
  .premise p {
    margin: 0; font-family: var(--sans); font-size: 19px; font-weight: 600;
    line-height: 1.32; letter-spacing: -0.014em;
  }

  /* ---- index ---------------------------------------------------- */
  .index {
    border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
    padding: 15px 0; margin: 28px 0 6px; max-width: var(--measure);
  }
  .index ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
  .index li { display: grid; grid-template-columns: 30px 1fr; gap: 12px; max-width: none; }
  .index .n {
    font-family: var(--mono); font-size: 9.5px; font-weight: 600; color: var(--hot);
    letter-spacing: .08em; font-variant-numeric: tabular-nums; padding-top: 2px;
  }
  .index a {
    font-family: var(--sans); font-size: 14.5px; font-weight: 600;
    letter-spacing: -0.01em; color: var(--ink);
    text-decoration: none; border-bottom: 1px solid var(--rule-soft);
  }
  .index a:hover, .index a:focus-visible { border-bottom-color: var(--hot); color: var(--hot); }

  /* ---- prose ---------------------------------------------------- */
  h2 {
    font-family: var(--sans); font-size: 25px; font-weight: 700;
    letter-spacing: -0.02em; line-height: 1.16; text-wrap: balance;
    margin: 44px 0 14px; padding-bottom: 8px; border-bottom: 1px solid var(--rule);
    display: grid; grid-template-columns: 42px 1fr; gap: 6px; align-items: baseline;
  }
  h2 .n {
    font-family: var(--mono); font-size: 10.5px; font-weight: 600; color: var(--hot);
    letter-spacing: .1em; font-variant-numeric: tabular-nums;
  }
  h3 {
    font-family: var(--sans); font-size: 17.5px; font-weight: 650;
    letter-spacing: -0.012em; margin: 32px 0 8px; color: var(--ink);
  }
  /* Sized UP from a first pass at 14.5px, which rendered SMALLER than the
     16.5px serif body it heads and read as a caption. A subhead must outrank
     its own prose; the hierarchy against h3 (17.5px/650) is carried by size and
     by the heavier weight, not by going under the body. */
  h4 {
    font-family: var(--sans); font-size: 15.5px; font-weight: 700;
    letter-spacing: -0.008em; margin: 28px 0 7px; color: var(--ink);
  }
  p { margin: 0 0 12px; }
  strong { font-weight: 650; }
  .lede::first-letter {
    font-family: var(--sans); font-weight: 700; font-size: 3.05em;
    float: left; line-height: .84; padding: 3px 8px 0 0; color: var(--hot);
  }
  .pull {
    font-family: var(--sans); font-style: normal;
    font-size: 19px; font-weight: 600; line-height: 1.32; letter-spacing: -0.014em;
    border-left: 3px solid var(--hot); padding: 3px 0 3px 15px;
    margin: 18px 0; color: var(--ink); max-width: var(--measure);
    box-sizing: border-box; text-wrap: pretty;
  }

  /* ---- data tables: the county bid-sheet register ---------------- */
  .tw { overflow-x: auto; margin: 16px 0 20px; max-width: var(--measure); }
  table { border-collapse: collapse; width: 100%; min-width: 460px;
          font-family: var(--mono); font-size: 12px; }
  caption {
    text-align: left; font-family: var(--mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3);
    padding-bottom: 7px;
  }
  th, td {
    padding: 6px 10px 6px 0; text-align: right; vertical-align: baseline;
    font-variant-numeric: tabular-nums; white-space: nowrap;
    border-bottom: 1px solid var(--rule-soft);
  }
  th:first-child, td:first-child { text-align: left; white-space: normal; }
  thead th {
    color: var(--ink-3); font-weight: 600; letter-spacing: .04em;
    border-bottom: 1px solid var(--rule); white-space: normal;
  }
  /* a floor on the label column so "expensive half, $134,000 and up" holds one
     line, without starving the numeric columns of the remaining width */
  th:first-child, td:first-child { min-width: 232px; }
  tbody tr:last-child td { border-bottom: 1px solid var(--rule); }
  tr.hi td { background: var(--hot-wash); }
  td strong, th strong { font-weight: 700; }

  /* ---- §05 buyer breakouts ---------------------------------------- */
  .bp {
    max-width: var(--measure); margin: 26px 0; padding: 16px 18px 14px;
    border: 1px solid var(--rule); background: var(--panel);
    break-inside: avoid; page-break-inside: avoid;
  }
  .bp-head { border-bottom: 2px solid var(--ink); padding-bottom: 7px; margin-bottom: 10px; }
  .bp h4 {
    font-family: var(--sans); font-size: 18px; font-weight: 700;
    letter-spacing: -0.015em; margin: 0;
  }
  .bp-sub {
    font-family: var(--mono); font-size: 11px; color: var(--ink-3);
    margin-top: 4px; font-variant-numeric: tabular-nums;
  }
  .bp-ops { font-size: 13px; color: var(--ink-2); margin: 0 0 12px; max-width: none; }
  .bp-half { margin-top: 12px; }
  .bp-half-h {
    font-family: var(--mono); font-size: 10px; font-weight: 700;
    letter-spacing: .12em; text-transform: uppercase;
  }
  .bp-half-h.cool { color: var(--cool-ink); }
  .bp-half-h.hot { color: var(--hot); }
  .bp-tot { font-size: 13px; color: var(--ink-2); margin: 3px 0 7px; max-width: none; }
  .bp-none { font-size: 13px; color: var(--ink-3); margin: 3px 0 0; }
  .bp-where {
    font-family: var(--mono); font-size: 11px; color: var(--ink-2);
    margin: 0 0 7px; line-height: 1.5;
  }
  .bp-wl {
    font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
    font-size: 9.5px; color: var(--ink-3);
  }
  .bp-n { color: var(--ink-3); font-variant-numeric: tabular-nums; }
  table.bp-t { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 11.5px; }
  table.bp-t th {
    text-align: right; font-weight: 600; color: var(--ink-3);
    border-bottom: 1px solid var(--rule); padding: 4px 8px 5px; font-size: 9.5px;
    letter-spacing: .03em; text-transform: uppercase; vertical-align: bottom;
    line-height: 1.25;
  }
  .bp-th2 { font-weight: 400; text-transform: none; letter-spacing: 0; }
  table.bp-t th:first-child, table.bp-t td:first-child { text-align: left; }
  /* The global first-column floor (232px) exists for section 02's long row
     labels. A four-digit year does not need it, and inheriting it was what
     pushed these eleven columns into a horizontal scroll. */
  table.bp-t th:first-child, table.bp-t td:first-child { min-width: 0; width: 3.4em; }
  table.bp-t td {
    text-align: right; padding: 5px 8px; font-variant-numeric: tabular-nums;
    border-bottom: 1px solid var(--rule-soft); white-space: nowrap;
  }
  /* The four overbid columns sit under one spanning label so the word does not
     repeat three times down the header and stack it four lines deep. */
  .bp-t .bp-g {
    text-align: center; font-size: 9px; letter-spacing: .09em;
    color: var(--ink-3); font-weight: 600; padding-bottom: 3px;
    border-bottom: 1px solid var(--rule-soft);
  }
  .bp-t .bp-g0 { border-bottom: none; padding: 0; }
  table.bp-t tr:first-child td.bp-g0 { width: auto; min-width: 0; }
  /* eleven columns will not fit a phone; let the table scroll rather than
     shrink the type below legibility */
  .bp-scroll { overflow-x: auto; }

  /* ---- figures --------------------------------------------------- */
  figure { margin: 24px 0 20px; max-width: var(--measure); }
  .fig-head { border-bottom: 1px solid var(--rule); padding-bottom: 7px; margin-bottom: 12px; }
  .fig-n {
    font-family: var(--mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: .11em; text-transform: uppercase; color: var(--hot);
  }
  .fig-title {
    font-family: var(--sans); font-size: 17px; font-weight: 650;
    letter-spacing: -0.012em; margin-top: 3px;
  }
  /* A RECAP is a figure the reader has already met, re-shown where a later
     argument turns on it. It must not compete with a first appearance: muted
     eyebrow, quieter rule, and a left margin rule marking it as a callback. */
  .fig-recap { border-left: 2px solid var(--rule); padding-left: 16px; }
  .fig-recap .fig-n { color: var(--ink-3); }
  .fig-recap .fig-head { border-bottom-color: var(--rule-soft); }
  figcaption {
    font-size: 13.5px; color: var(--ink-3); margin-top: 10px;
    line-height: 1.5; max-width: var(--measure);
  }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  /* below this width the chart's type shrinks past legibility, so the chart
     scrolls at a readable size instead of scaling down with the column */
  .fig-scroll { overflow-x: auto; }
  @media (max-width: 660px) { .fig-scroll > svg { min-width: 600px; } }
  .fig-map .fig-scroll > svg { min-width: 0; }
  .grid-line { stroke: var(--rule-soft); stroke-width: 1; }
  .axis-line { stroke: var(--rule); stroke-width: 1; }
  .tick {
    font-family: var(--mono); font-size: 9.5px; fill: var(--ink-3);
    font-variant-numeric: tabular-nums;
  }
  .series-label { font-family: var(--sans); font-size: 11px; font-weight: 650; }
  .val-label {
    font-family: var(--mono); font-size: 9.5px; fill: var(--ink-2);
    font-variant-numeric: tabular-nums; font-weight: 600;
  }
  .dot-ring { stroke: var(--ground); stroke-width: 2; }
  .strap { font-family: var(--sans); font-size: 14px; font-weight: 700; fill: var(--ink);
           opacity: .72; }
  .legend { display: flex; gap: 18px; flex-wrap: wrap; margin: 10px 0 2px; }
  .legend span {
    display: inline-flex; align-items: center; gap: 6px;
    font-family: var(--mono); font-size: 10px; letter-spacing: .04em;
    text-transform: uppercase; color: var(--ink-2);
  }
  .swatch { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
  /* the map is nearly square, so it gets a narrower column than a wide chart */
  .fig-map svg { max-width: 500px; margin-inline: auto; }
  /* a halo so a value sitting on a guide line or a shape stays legible */
  .halo { paint-order: stroke; stroke: var(--ground); stroke-width: 3px; stroke-linejoin: round; }
  .maplbl { font-weight: 700; fill: var(--ink); paint-order: stroke;
            stroke: var(--ground); stroke-width: 3px; stroke-linejoin: round; }
  .zipshape { transition: fill-opacity .12s ease; }
  .zipshape:hover { fill-opacity: .95; cursor: crosshair; }
  .zipshape:focus { outline: none; }
  .zipshape:focus-visible { outline: 2px solid var(--hot); outline-offset: 1px; }
  @media (prefers-reduced-motion: reduce) { .zipshape { transition: none; } }

  /* ---- the four-group quadrant explainer -------------------------- */
  .qaxis {
    font-family: var(--mono); font-size: 9.5px; letter-spacing: .1em;
    text-transform: uppercase; color: var(--ink-3); margin-bottom: 5px;
  }
  /* minmax(0,…) not 1fr: a grid track's auto minimum is min-content, and the
     cards' mono type would otherwise push the two columns past the measure */
  .quad {
    display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px;
    max-width: var(--measure);
  }
  .qhead {
    font-family: var(--mono); font-size: 10px; font-weight: 600;
    letter-spacing: .09em; text-transform: uppercase; color: var(--ink-2);
    padding-bottom: 5px; border-bottom: 1px solid var(--rule);
  }
  .qcard {
    border: 1px solid var(--rule); background: var(--panel); min-width: 0;
    padding: 13px 15px 11px; box-sizing: border-box; break-inside: avoid;
  }
  .qeyebrow {
    font-family: var(--mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: .09em; text-transform: uppercase;
  }
  .qeyebrow span { opacity: .5; }
  .qbig {
    font-family: var(--sans); font-size: 30px; font-weight: 680;
    letter-spacing: -0.02em; line-height: 1.05; margin: 5px 0 2px;
    font-variant-numeric: tabular-nums;
  }
  .qsub { font-size: 12.5px; color: var(--ink-2); line-height: 1.35; }
  .qnote {
    font-family: var(--serif); font-size: 13px; line-height: 1.42;
    margin: 9px 0 0; padding-top: 9px; border-top: 1px solid var(--rule-soft);
  }
  .qstats {
    display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 9px;
    font-family: var(--mono); font-size: 10px; letter-spacing: .04em;
    text-transform: uppercase; color: var(--ink-3);
  }
  .qstats strong { color: var(--ink); font-weight: 700; }
  /* min-width:0 undoes the 460px floor the report's data tables carry — this
     one lives inside a half-measure card, not across the page */
  .qlad { width: 100%; min-width: 0; margin: 9px 0 0; border-collapse: collapse;
          table-layout: fixed; }
  .qlad th { width: 34%; }
  .qlad td { width: 33%; }
  .qlad caption {
    font-family: var(--mono); font-size: 9px; letter-spacing: .1em;
    text-transform: uppercase; color: var(--ink-3); text-align: left;
    padding-bottom: 3px; border-top: 1px solid var(--rule-soft); padding-top: 8px;
  }
  .qlad th, .qlad td {
    font-family: var(--mono); font-size: 11px; font-variant-numeric: tabular-nums;
    padding: 2px 0; text-align: right; border: 0; color: var(--ink-2);
  }
  .qlad th { text-align: left; font-weight: 600; color: var(--ink-3); }
  .qlad thead th { font-size: 9px; letter-spacing: .06em; text-transform: uppercase;
                   color: var(--ink-3); font-weight: 600; padding-bottom: 4px; }
  .qlad thead th.qh { text-align: right; }
  .qlad .qd { color: var(--ink); font-weight: 600; }
  /* the page's data tables close on a rule and floor their label column; this
     one is a card insert, so it does neither */
  .qlad tbody tr:last-child td { border-bottom: 0; }
  .qlad th:first-child, .qlad td:first-child { min-width: 0; }
  @media (max-width: 620px) { .quad { grid-template-columns: 1fr; }
                            .qhead, .qaxis { display: none; } }

  /* ---- pending / caveat ------------------------------------------ */
  .pending {
    border: 1px solid var(--rule); border-left: 3px solid var(--cool);
    background: var(--panel); padding: 13px 16px; margin: 20px 0;
    max-width: var(--prose); box-sizing: border-box; font-size: 15.5px;
  }
  .pending .lbl {
    font-family: var(--mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: .11em; text-transform: uppercase; color: var(--cool-ink);
    display: block; margin-bottom: 6px;
  }
  .pending p { margin: 0; }

  /* ---- editor's note ----------------------------------------------
     The publication speaking to the reader, not a caveat about the
     data. Warm rule rather than the cool one .pending uses, and no
     panel fill, so it reads as an aside in the margin of the argument
     rather than as another box of findings. */
  .ednote {
    border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
    border-left: 3px solid var(--hot); padding: 13px 16px; margin: 26px 0;
    max-width: var(--prose); box-sizing: border-box; font-size: 15.5px;
  }
  .ednote .lbl {
    font-family: var(--mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: .11em; text-transform: uppercase; color: var(--hot);
    display: block; margin-bottom: 6px;
  }
  .ednote p { margin: 0; }
  /* a multi-paragraph note needs air between its paragraphs, but not before the
     first one — the label already sets that gap */
  .ednote p + p, .pending p + p { margin-top: 10px; }

  ul { padding-left: 18px; margin: 0 0 12px; }
  ul li { margin-bottom: 6px; }

  /* ---- close ------------------------------------------------------ */
  .method {
    margin-top: 40px; padding-top: 14px; border-top: 2px solid var(--ink);
    font-family: var(--sans); font-size: 11px; line-height: 1.5;
    color: var(--ink-3); max-width: none;
  }
  .promise {
    font-family: var(--serif); font-style: italic; font-size: 12.5px;
    color: var(--ink-3); margin-top: 12px;
  }
  a { color: var(--hot); text-decoration: none; border-bottom: 1px solid currentColor; }
  a:focus-visible { outline: 2px solid var(--cool); outline-offset: 3px; }

  /* ---- tooltip ---------------------------------------------------- */
  #tip {
    position: fixed; pointer-events: none; opacity: 0; z-index: 9;
    background: var(--ink); color: var(--ground);
    font-family: var(--mono); font-size: 10.5px; line-height: 1.5;
    padding: 6px 9px; border-radius: 3px; white-space: nowrap;
    transition: opacity .1s ease; font-variant-numeric: tabular-nums;
  }
  .hit { fill: transparent; cursor: crosshair; }
  @media (prefers-reduced-motion: reduce) { #tip { transition: none; } * { transition: none !important; } }

  /* ---- print: this is a mailed report, so print is a real layout -- */
  @page { size: letter; margin: 14mm 15mm; }
  @media print {
    :root {
      --measure: 178mm;   /* the usable width inside the @page margins */
      --prose: 118mm;     /* ~72 characters at 9.6pt */
      --ground: #fff; --panel: #fff; --ink: #14171C; --ink-2: #3D444F;
      --ink-3: #5C6470; --rule: #C9CCC7; --rule-soft: #E2E4E0;
      --hot: #A93316; --cool: #00788F; --cool-ink: #005D71; --ochre: #7E6D10;
    }
    body { padding: 0; font-size: 9.6pt; line-height: 1.46; background: #fff; }
    .sheet { max-width: none; }
    .mast { padding: 0 0 6px; }
    h1 { font-size: 22pt; margin-top: 12px; }
    .dateline { font-size: 7.6pt; margin: 10px 0 3px; }
    .premise { margin-top: 14px; }
    .premise p { font-size: 11pt; }
    .index { margin: 16px 0 4px; padding: 10px 0; break-inside: avoid; }
    .index a { font-size: 9.4pt; }
    h2 { font-size: 14pt; margin: 22px 0 8px; break-after: avoid; }
    h3 { font-size: 11pt; margin: 16px 0 5px; break-after: avoid; }
    h4 { font-size: 10.4pt; margin: 14px 0 4px; break-after: avoid; }
    p { margin: 0 0 7px; }
    .pull { font-size: 11pt; margin: 11px 0; padding-left: 11px; break-inside: avoid; }
    .tw { margin: 10px 0 12px; break-inside: avoid; }
    table { font-size: 8pt; }
    th, td { padding: 3px 8px 3px 0; }
    figure { margin: 14px 0 0; break-inside: avoid; }
    figure svg { max-height: 78mm; }
    .quad { gap: 6px; }
    .qcard { padding: 8px 10px 7px; }
    .qbig { font-size: 17pt; }
    .qsub { font-size: 8pt; }
    .qnote { font-size: 8.2pt; margin-top: 6px; padding-top: 6px; }
    .qstats { font-size: 7pt; margin-top: 6px; }
    .qlad th, .qlad td { font-size: 7.6pt; padding: 1px 0; }
    .qlad caption { font-size: 6.6pt; padding-top: 5px; }
    .fig-map svg { max-height: 118mm; max-width: 118mm; }
    .fig-title { font-size: 11pt; }
    figcaption { font-size: 7.8pt; margin-top: 5px; line-height: 1.32; }
    .tick, .val-label { font-size: 8.5px; }
    .series-label { font-size: 10px; }
    .legend { margin: 5px 0 1px; gap: 12px; }
    .legend span { font-size: 6.8pt; }
    .pending, .ednote { padding: 9px 12px; break-inside: avoid; }
    .method { font-size: 7.2pt; line-height: 1.36; margin-top: 18px; padding-top: 8px; }
    #tip { display: none; }
    .fig-scroll { overflow: visible; }
    .fig-scroll > svg { min-width: 0; }
    a { color: var(--ink); border-bottom: 0; }
  }
</style>"""
