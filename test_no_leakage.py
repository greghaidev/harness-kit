#!/usr/bin/env python3
"""This kit must not name the operator's other work, or the machine it was extracted from.

WHY THIS IS A TEST AND NOT A REVIEW

The kit was extracted from a personal harness that runs an unrelated side project. The first
sanitization pass rewrote 16 files. A second pass caught residue the first missed. A third
caught residue the second missed. Three passes, by someone paying attention, and each one
found something — which is exactly the shape of a job that must not be done by attention.

The failure mode is not one big mistake. It is one file copied across later, carrying a
comment nobody read, into a repository that colleagues can see. So the rule is mechanical and
it runs on every commit.

WHAT IS FORBIDDEN, AND WHY EACH ONE

  * the side project's domain vocabulary — the work has nothing to do with this kit, and a
    stray fixture naming it is a disclosure, not a typo
  * the operator's own name and home paths — this is a professional artifact
  * home infrastructure hostnames — they identify a private network

A term is added here the moment it is found once. The list is cheap; the disclosure is not.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent

# Matched case-insensitively, as whole words where a word boundary is meaningful.
FORBIDDEN = [
    # the side project
    r"\bmosaic\b", r"\bcasenet\b", r"\bscraper\b", r"\btaxsale\b", r"tax[- ]sale",
    r"\bprobate\b", r"\bobituar", r"\bsmartskip\b", r"\bskiptrace\b", r"\bheritage\b",
    r"\bpre-?foreclosure\b", r"\beviction\b", r"\bdecedent\b", r"\bparcel\b",
    r"\bmissouri\b", r"st\.? ?louis", r"\bstl\b",
    # the operator, by name, and their paths
    r"\bgreg\b", r"/home/greg",
    # home infrastructure
    r"\bgem12\b", r"elite-control", r"\bdroplet\b", r"digitalocean", r"\boculink\b",
    r"\btailscale\b", r"pi-?hole", r"\bagent_os\b", r"/srv/agent-os",
    # the private tenant of the original store
    r"\bfamily-private\b", r"family tenant", r"family wall",
    # provenance: this kit must not advertise where it came from, or that a
    # second model lineage exists anywhere the operator can reach
    r"\bat home\b", r"home harness", r"side project", r"side job",
    r"\bglm-", r"\bgrok\b", r"\bkimi\b", r"gpt-5", r"\bhy3\b", r"\bdeepseek\b",
    r"\bopenrouter\b", r"executive board", r"board-options",
    # gendered third person: this is a professional artifact addressed to "you"
    # or "the operator", and a stray "he" identifies its subject
    r"\bhis\b", r"\bhim\b",
]

# Extensions worth reading. Fonts and images are binary and carry no prose.
TEXT_SUFFIXES = {".py", ".sh", ".md", ".json", ".txt", ".yaml", ".yml", ".toml", ".cfg"}

# This file necessarily contains every forbidden term, as the list.
SELF = pathlib.Path(__file__).name


def _files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        if p.name == SELF or "__pycache__" in p.parts or "fonts" in p.parts:
            continue
        yield p


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_term_appears_nowhere_in_the_kit(pattern):
    rx = re.compile(pattern, re.I)
    hits = []
    for p in _files():
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.split("\n"), 1):
            # `font-family` is CSS, not the private tenant.
            if "font-family" in line.lower():
                continue
            if rx.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
    assert not hits, (
        f"{pattern!r} appears in the kit — this repository is visible to colleagues.\n  "
        + "\n  ".join(hits[:12]))


def test_the_guard_actually_reads_files():
    """A guard that scans nothing passes everything.

    The absence-test trap: `assert no hits` is satisfied just as well by a broken file walk
    as by a clean repository, and it degrades silently. So assert the walk is real.
    """
    files = list(_files())
    assert len(files) > 20, f"only {len(files)} files scanned — the walk is broken"
    assert any(p.suffix == ".py" for p in files)
    assert any(p.suffix == ".md" for p in files)


def test_the_guard_would_catch_a_planted_term(tmp_path):
    """Red-green on demand: prove the matcher fires rather than trusting that it would."""
    rx = re.compile(r"\bgreg\b", re.I)
    assert rx.search("written by Greg on Tuesday")
    assert not rx.search("aggregate the rows")      # substring must not false-positive
