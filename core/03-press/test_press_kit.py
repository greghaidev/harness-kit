#!/usr/bin/env python3
"""Tests for the press and its claim gate.

The point of most of these is not that the feature works — it is that a specific
failure mode STAYS closed. Two of them (the directive guard and the heading guard)
each cost 22 minutes and 4.3 GB in the press this was ported from, produced no
error and no partial output, and were reached by different doors.
"""
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claims  # noqa: E402
import press  # noqa: E402

def _kit_root(start: pathlib.Path) -> pathlib.Path:
    """Walk up to the directory that owns this kit, by marker rather than by depth.

    A hardcoded `parents[N]` is correct for exactly one directory layout. This kit was
    extracted from a deeper tree, and the count that was right there resolved to the
    user's home directory here — so every claim probe looked for files one level above
    the artifact under test. Markers survive the move; depth counts do not.
    """
    for d in (start, *start.parents):
        if (d / "INSTALL.md").exists() and (d / "verify.py").exists():
            return d
    return start.parents[1]


REPO = _kit_root(HERE)


def r(md, **kw):
    kw.setdefault("title", "T")
    kw.setdefault("wordmark", "W")
    kw.setdefault("eyebrow", "")
    return press.render(md, REPO, **kw)


# ------------------------------------------------------------------ grammar
def test_every_block_type_parses():
    blocks = press.parse_blocks(
        "## 00 · A\n\ntext\n\n!! pulled\n\n- one\n- two\n\n> **Note.** x\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\ncode\n```\n\n### cross\n\n#### sub".split("\n"))
    kinds = [b[0] for b in blocks]
    assert kinds == ["h2", "p", "pull", "ul", "quote", "table", "code", "h3", "h4"]


def test_unrecognised_directive_fails_loudly_instead_of_hanging():
    # the 4.3GB hang, door one
    with pytest.raises(SystemExit) as e:
        press.parse_blocks(["<!-- fig: something -->"])
    assert "unrecognised directive" in str(e.value)


def test_unhandled_heading_level_fails_loudly_instead_of_hanging():
    # the 4.3GB hang, door two
    with pytest.raises(SystemExit) as e:
        press.parse_blocks(["# a top-level heading"])
    assert "unhandled heading" in str(e.value)


def test_paragraph_collector_can_never_fail_to_advance():
    """The shape of both hangs: a line no branch consumes and the collector refuses."""
    for line in ["<!-- x -->", "# x", "##### x"]:
        with pytest.raises(SystemExit):
            press.parse_blocks([line])


def test_fenced_code_survives_markdown_special_characters():
    blocks = press.parse_blocks("```\n**not bold** | not a table\n```".split("\n"))
    assert blocks == [("code", "", "**not bold** | not a table")]
    doc, _, _ = r("```\n**not bold**\n```")
    # Scope the probe to the rendered block. Asserting over the whole document
    # matches the embedded stylesheet, which quotes "<strong>" in a comment —
    # a probe that hits something incidental is not a probe.
    block = doc.split('<pre class="src"')[1].split("</pre>")[0]
    assert "<strong>" not in block
    assert "**not bold**" in block


# ------------------------------------------------------------------ furniture
def test_pull_statements_cannot_be_orphaned_by_an_edit():
    """The reason this press uses a marker instead of substring matching.

    In the press this was ported from, a pull quote was styled by matching a
    prefix from a hardcoded list; editing the sentence dropped the styling with
    no error. A marker moves with the sentence.
    """
    doc, _, stats = r("## 00 · A\n\nlede\n\n!! this sentence carries the argument")
    assert stats["pulls"] == 1
    doc2, _, stats2 = r("## 00 · A\n\nlede\n\n!! this sentence now says something else entirely")
    assert stats2["pulls"] == 1


def test_numbered_section_gets_a_kicker_and_appendix_does_not():
    doc, _, _ = r("## 00 · Numbered\n\nx\n\n## Method\n\ny")
    assert 'class="n">Section 00<' in doc
    assert 'class="h2-app"' in doc
    assert 'class="n">Appendix<' in doc


def test_section_ends_on_an_end_mark():
    doc, _, _ = r("## 00 · A\n\nonly paragraph")
    assert press.ENDMARK in doc


def test_a_section_closing_on_a_list_gets_the_mark_on_its_own_line():
    doc, _, _ = r("## 00 · A\n\np\n\n- item")
    assert 'class="em-line"' in doc


def test_small_caps_opener_never_ends_on_an_abbreviation():
    out = press.small_caps_opener("<p>Compiled from county records here.</p>")
    head = out.split('class="sc">')[1].split("</span>")[0]
    assert not head.rstrip().lower().endswith("st.")


def test_heading_anchor_is_stable_and_truncated():
    assert press.anchor_for("00 · A Very Long Heading That Runs On And On And On") == \
        press.anchor_for("00 · A Very Long Heading That Runs On And On And On")
    assert len(press.anchor_for("x" * 200)) <= 42


# ------------------------------------------------------------------ claim gate
def test_claim_gate_resolves_every_verb():
    md = ("{{path:README.md}} {{lines:README.md}} "
          "{{count:core/03-press/*.py}} {{exists:INSTALL.md}} "
          "{{contains:INSTALL.md#operator}}")
    out, problems = claims.resolve(md, REPO)
    assert problems == []
    assert "README.md" in out
    assert any(ch.isdigit() for ch in out)


def test_claim_gate_FAILS_when_a_path_stops_existing():
    """The property the whole gate exists for."""
    out, problems = claims.resolve("{{path:does_not_exist_xyz.py}}", REPO)
    assert len(problems) == 1
    assert "no such path" in problems[0]
    assert "BROKEN CLAIM" in out          # and it leaves a visible scar in the output


def test_claim_gate_collects_every_problem_not_just_the_first():
    md = "{{path:nope_a.py}} {{path:nope_b.py}} {{lines:nope_c.py}}"
    _, problems = claims.resolve(md, REPO)
    assert len(problems) == 3


def test_claim_gate_refuses_to_escape_the_permitted_roots():
    _, problems = claims.resolve("{{path:../../../etc/passwd}}", REPO)
    assert len(problems) == 1
    assert "escapes the permitted roots" in problems[0]


def test_an_allowlisted_external_root_resolves():
    """The memory store is a separate git repo at a system path. A book about this
    harness cannot make one true statement about it without leaving the working tree,
    so the allowlist is load-bearing rather than a convenience."""
    import pathlib as _p
    root = _p.Path(claims.EXTERNAL_ROOTS[0]) if claims.EXTERNAL_ROOTS else None
    if not root or not root.is_dir():
        pytest.skip("no allowlisted external root present on this machine")
    if not list(root.rglob("*.md")):
        pytest.skip("allowlisted root has no markdown to count")
    out, problems = claims.resolve("{{count:%s/**/*.md}}" % root, REPO)
    assert problems == []
    assert out.strip().replace(",", "").isdigit()


def test_an_absolute_path_outside_the_allowlist_is_still_refused():
    _, problems = claims.resolve("{{path:/etc/passwd}}", REPO)
    assert len(problems) == 1
    _, problems2 = claims.resolve("{{count:/etc/*.conf}}", REPO)
    assert len(problems2) == 1


def test_a_handler_that_crashes_becomes_a_reported_problem_not_a_dead_build():
    """One malformed token in a 200-page document must not destroy the run: the author
    needs to see the other nineteen problems in the same pass."""
    out, problems = claims.resolve("{{quote:README.md}}", REPO)   # missing #SYMBOL
    assert len(problems) == 1
    assert "BROKEN CLAIM" in out


def test_line_count_matches_wc_l():
    import subprocess
    out, problems = claims.resolve("{{lines:README.md}}", REPO)
    assert problems == []
    wc = int(subprocess.run(["wc", "-l", str(REPO / "README.md")],
                            capture_output=True, text=True).stdout.split()[0])
    assert out.strip() == f"{wc:,}"


def test_quote_extracts_a_live_symbol_and_fails_on_a_renamed_one():
    out, problems = claims.resolve(
        "{{quote:core/02-session/session_codename_lib.py#codename_for}}", REPO)
    assert problems == []
    assert "def codename_for" in out
    _, problems2 = claims.resolve(
        "{{quote:core/02-session/session_codename_lib.py#renamed_away}}", REPO)
    assert len(problems2) == 1


def test_contains_catches_a_literal_that_has_been_removed():
    _, problems = claims.resolve(
        "{{contains:README.md#a phrase that is definitely not in the file}}", REPO)
    assert len(problems) == 1


def test_symbol_extraction_stops_at_the_next_top_level_block():
    src = "def a():\n    x = 1\n    return x\n\n\ndef b():\n    return 2\n"
    got = claims._extract_symbol(src, "a")
    assert "def b" not in got
    assert "return x" in got


def test_a_document_that_makes_no_claims_reports_zero():
    _, _, stats = r("## 00 · A\n\nplain prose")
    assert stats["claims"] == 0


def test_claims_are_counted_before_they_are_substituted_away():
    """The counter is the gate's denominator; counting after substitution reads 0."""
    _, _, stats = r("## 00 · A\n\n{{lines:README.md}} and {{path:README.md}}")
    assert stats["claims"] == 2


# ------------------------------------------------------------------ plates
def test_a_missing_plate_is_a_warning_not_a_build_failure():
    """The images arrive asynchronously; the book must build before they land."""
    doc, problems, stats = r("## 00 · A\n\nx")
    assert isinstance(doc, str) and len(doc) > 0
    assert not any("BROKEN CLAIM" in p for p in problems)


def test_render_is_deterministic():
    md = "## 00 · A\n\nsome prose\n\n!! a pull\n\n- a\n- b"
    assert r(md)[0] == r(md)[0]
