#!/usr/bin/env python3
"""Tests for memory hygiene.

The property under test is not "the sweep counts follow-ups". It is the one claim that would
make this component WORSE than nothing if it were false:

    nothing closes on an inference.

A hygiene pass that closes a live commitment because a note happened to mention a merged PR does
not save you effort — it silently deletes work, and you will never find out which. So every test
below is about a REFUSAL: what must stay open, and what must be reported as unrunnable rather
than rendered as clean.

The three gates each have a test because each one was put there by a real false positive:
a note citing a PR as the origin of a finding; a note written by the session that shipped the PR;
and a note whose own words say the PR left the work undone.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

KIT = pathlib.Path(__file__).resolve().parents[2]
HYGIENE = KIT / "core" / "06-hygiene" / "hygiene.py"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def env():
    """A throwaway store, a throwaway repo, and a fake PR CLI that answers from files.

    The PR CLI is faked rather than mocked in-process because the corroboration gates read its
    output through a subprocess boundary, and a test that skips that boundary is not testing the
    thing that runs.
    """
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for t in ("work", "meta"):
            (d / t).mkdir()
            _git(d / t, "init", "-q")
            _git(d / t, "config", "user.email", "a@b.c")
            _git(d / t, "config", "user.name", "t")
        repo = d / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")

        prs = d / "prs"
        prs.mkdir()
        # A Python script run by this interpreter, not a `#!/bin/sh` file: Windows cannot execute a
        # shell script by name, and pr_cli accepts an argv list for exactly that case.
        gh = d / "fake_gh.py"
        gh.write_text(
            "import pathlib, sys\n"
            f"prs = pathlib.Path({str(prs)!r})\n"
            "cmd = sys.argv[2] if len(sys.argv) > 2 else ''\n"
            "if cmd == 'list':\n"
            "    p = prs / 'list.json'\n"
            "    print(p.read_text(encoding='utf-8') if p.exists() else '[]')\n"
            "elif cmd == 'view':\n"
            "    p = prs / f'pr-{sys.argv[3]}.json'\n"
            "    if not p.exists():\n"
            "        sys.exit(1)\n"
            "    print(p.read_text(encoding='utf-8'))\n", encoding="utf-8")

        (d / "lanes.json").write_text(json.dumps({
            "tenant": "work", "repo": str(repo), "pr_cli": [sys.executable, str(gh)],
        }), encoding="utf-8")

        e = {**os.environ, "HARNESS_HOME": str(KIT),
             "HARNESS_WORK_STORE": str(d / "work"), "HARNESS_META_STORE": str(d / "meta"),
             "HARNESS_LANES_CONFIG": str(d / "lanes.json"),
             "HARNESS_HYGIENE_STATE": str(d / "state"), "_D": str(d)}
        yield e


def run(env, *args, expect=None):
    r = subprocess.run([sys.executable, str(HYGIENE), *args],
                       capture_output=True, text=True, env=env, timeout=120, encoding="utf-8", errors="replace")
    if expect is not None:
        assert r.returncode == expect, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    return r


def put_note(env, **note):
    """Write a note straight through the store, the way any component would."""
    code = (f"import sys; sys.path.insert(0, {str(KIT / 'core' / '01-memory')!r});\n"
            f"import agentos_store as s; import json;\n"
            f"print(s.put(json.loads({json.dumps(json.dumps(note))}))['id'])")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def followup(env, nid, title, body, created=None):
    note = {"id": nid, "title": title, "type": "episodic", "tenant": "work",
            "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
            "tags": ["follow-up"], "body": body}
    if created:
        note["created"] = _iso(created)
    return put_note(env, **note)


def set_prs(env, index, views=None):
    d = pathlib.Path(env["_D"]) / "prs"
    (d / "list.json").write_text(json.dumps(
        [{"number": n, "state": s} for n, s in index.items()]), encoding="utf-8")
    for num, view in (views or {}).items():
        (d / f"pr-{num}.json").write_text(json.dumps(view), encoding="utf-8")


def report(env):
    state = pathlib.Path(env["_D"]) / "state"
    return "\n".join(p.read_text(encoding="utf-8") for p in state.glob("report-*.md"))


# ─────────────────────────────────────────────────────────── the mis-tag class is PRECISE

def test_a_status_note_tagged_followup_is_closable(env):
    """'SHIPPED: the new export' with nothing to do is the class that fills a store."""
    followup(env, "f-shipped", "SHIPPED — the quarterly export", "It went out on Tuesday.")
    set_prs(env, {})
    run(env, "sweep", "--no-heartbeat", expect=0)
    assert "f-shipped" in report(env)
    r = run(env, "reconcile", expect=0)
    assert "RECLASS    f-shipped" in r.stdout


def test_a_live_action_with_a_terminal_sounding_title_is_NOT_closable(env):
    """The precision property, and the reason the body is consulted at all.

    Title matching alone flagged 10 of 18 genuine actions on a real store, because a title tells
    you what a note is ABOUT and only the body tells you whether anything is left to do.
    """
    followup(env, "f-live", "Backfill the CLOSED accounts in the customer dimension",
             "ONE ACTION: rerun the backfill for accounts closed before March.")
    set_prs(env, {})
    run(env, "sweep", "--no-heartbeat", expect=0)
    r = run(env, "reconcile", expect=0)
    assert "RECLASS    f-live" not in r.stdout
    assert "0 note(s) would be superseded" in r.stdout


# ─────────────────────────────────────────────────────────── the three corroboration gates

def _old(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def test_gate_1_a_pr_that_changed_nothing_matching_the_action_does_not_close_it(env):
    """The measured false positive: a note cites a PR as CONTEXT, and it looks identical to one
    whose work that PR shipped. 15 of 15 such rows on a real store were false."""
    followup(env, "f-tokens", "Publish the retention policy at docs/retention.md",
             "ONE ACTION: write docs/retention.md. Noticed while reviewing PR #412.",
             created=_old(60))
    set_prs(env, {412: "MERGED"},
            {412: {"title": "Rework the billing export",
                   "files": [{"path": "etl/billing.sql"}, {"path": "etl/invoice.sql"}],
                   "mergedAt": _iso(_old(3))}})
    run(env, "sweep", "--no-heartbeat", expect=0)
    r = run(env, "reconcile", expect=0)
    assert "UNVERIFIED f-tokens" in r.stdout
    assert "changed nothing matching" in r.stdout
    assert "CLOSE      f-tokens" not in r.stdout


def test_gate_2_a_contemporaneous_pr_is_the_findings_origin_not_its_resolution(env):
    """A note written by the session that shipped the PR is recording what that PR did NOT do."""
    followup(env, "f-contemp", "Rotate the warehouse credentials in etl/secrets.yml",
             "ONE ACTION: rotate the key in etl/secrets.yml. Seen while shipping PR #500.",
             created=_old(10))
    set_prs(env, {500: "MERGED"},
            {500: {"title": "etl/secrets.yml loader rewrite",
                   "files": [{"path": "etl/secrets.yml"}],
                   "mergedAt": _iso(_old(10) + timedelta(hours=2))}})
    run(env, "sweep", "--no-heartbeat", expect=0)
    r = run(env, "reconcile", expect=0)
    assert "UNVERIFIED f-contemp" in r.stdout
    assert "origin, not its resolution" in r.stdout


def test_gate_3_the_note_says_in_its_own_words_that_the_pr_left_this_undone(env):
    followup(env, "f-origin", "Add the currency column to etl/dim_customer.sql",
             "ONE ACTION: add the column. PR #600 deliberately did not touch this.",
             created=_old(90))
    set_prs(env, {600: "MERGED"},
            {600: {"title": "etl/dim_customer.sql refactor",
                   "files": [{"path": "etl/dim_customer.sql"}],
                   "mergedAt": _iso(_old(2))}})
    run(env, "sweep", "--no-heartbeat", expect=0)
    r = run(env, "reconcile", expect=0)
    assert "UNVERIFIED f-origin" in r.stdout
    assert "left this undone" in r.stdout


def test_all_three_gates_passing_does_close_it(env):
    """The gates must not be so strict that nothing ever closes — then it is theater."""
    followup(env, "f-real", "Add the currency column to etl/dim_customer.sql",
             "ONE ACTION: add currency to etl/dim_customer.sql. Tracking in PR #700.",
             created=_old(90))
    set_prs(env, {700: "MERGED"},
            {700: {"title": "Add currency to the customer dimension",
                   "files": [{"path": "etl/dim_customer.sql"}],
                   "mergedAt": _iso(_old(2))}})
    run(env, "sweep", "--no-heartbeat", expect=0)
    r = run(env, "reconcile", expect=0)
    assert "CLOSE      f-real" in r.stdout
    r = run(env, "reconcile", "--apply", expect=0)
    assert "APPLIED" in r.stdout
    # And it is closed PASSIVELY, by supersession — the follow-up is not rewritten.
    after = run(env, "sweep", "--no-heartbeat", expect=0)
    assert '"open_followups": 0' in after.stdout or '"open_followups":0' in after.stdout


def test_a_closed_unmerged_pr_never_closes_anything(env):
    """CLOSED is not MERGED. The work was abandoned, so the follow-up is probably still live."""
    followup(env, "f-aband", "Ship the lineage diagram", "ONE ACTION: ship it. See PR #800.",
             created=_old(40))
    set_prs(env, {800: "CLOSED"})
    run(env, "sweep", "--no-heartbeat", expect=0)
    assert "CLOSED UNMERGED" in report(env)
    r = run(env, "reconcile", expect=0)
    assert "f-aband" not in r.stdout.replace("0 PR-referenced", "")


# ─────────────────────────────────────────────────── an unrunnable check reports itself

def test_reconcile_aborts_when_it_cannot_reach_the_pr_host(env):
    """An empty result from a check that never ran is indistinguishable from a clean store.

    This is the single most important refusal here: the failure mode it prevents is a hygiene
    pass that reports 'nothing to close' on a store full of closable work, forever.
    """
    followup(env, "f-x", "Something", "ONE ACTION: do it.")
    cfg = pathlib.Path(env["HARNESS_LANES_CONFIG"])
    cfg.write_text(json.dumps({**json.loads(cfg.read_text(encoding="utf-8")), "pr_cli": None}), encoding="utf-8")
    r = run(env, "reconcile", expect=2)
    assert "cannot corroborate" in r.stderr
    assert "SKIPPED run, not a clean one" in r.stderr


def test_the_sweep_says_so_when_ground_truth_was_unavailable(env):
    followup(env, "f-y", "Something", "ONE ACTION: do it.")
    cfg = pathlib.Path(env["HARNESS_LANES_CONFIG"])
    cfg.write_text(json.dumps({**json.loads(cfg.read_text(encoding="utf-8")), "pr_cli": None}), encoding="utf-8")
    r = run(env, "sweep", "--no-heartbeat", expect=0)
    assert "SKIPPED" in r.stdout
    assert "This is not a clean result" in report(env)


def test_an_unconfigured_retired_phrase_scan_reports_its_own_inertness(env):
    """A configured-empty check that prints nothing reads exactly like a clean result."""
    run(env, "sweep", "--no-heartbeat", expect=0)
    assert "NOT CONFIGURED" in report(env)
    assert "looked for nothing" in report(env)


# ─────────────────────────────────────────────────────────── THE RETARGET: conclusions

def test_a_conclusion_with_no_source_is_surfaced(env):
    put_note(env, id="c-1", title="CONCLUSION — churn is concentrated in the SMB tier",
             type="episodic", tenant="work", sensitivity="internal", egress="cloud-ok",
             status="committed", tags=["journal", "conclusion"],
             body="churn is concentrated in the SMB tier\n\nsource:\n")
    run(env, "sweep", "--no-heartbeat", expect=0)
    assert "c-1" in report(env)
    assert "cannot be defended when quoted back" in report(env)


def test_a_conclusion_whose_source_file_is_gone_is_surfaced(env):
    put_note(env, id="c-2", title="CONCLUSION — 41% of accounts renew late", type="episodic",
             tenant="work", sensitivity="internal", egress="cloud-ok", status="committed",
             tags=["journal", "conclusion"],
             body="41% of accounts renew late\n\nsource: analysis/renewals.sql\n")
    run(env, "sweep", "--no-heartbeat", expect=0)
    assert "no longer exists" in report(env)


def test_a_conclusion_whose_source_still_exists_is_not_flagged(env):
    repo = pathlib.Path(env["_D"]) / "repo"
    (repo / "analysis").mkdir(parents=True)
    (repo / "analysis" / "renewals.sql").write_text("select 1", encoding="utf-8")
    put_note(env, id="c-3", title="CONCLUSION — 41% of accounts renew late", type="episodic",
             tenant="work", sensitivity="internal", egress="cloud-ok", status="committed",
             tags=["journal", "conclusion"],
             body="41% renew late\n\nsource: analysis/renewals.sql\n")
    run(env, "sweep", "--no-heartbeat", expect=0)
    assert '"broken_source_conclusions": 0' in pathlib.Path(
        env["_D"], "state", "status.json").read_text(encoding="utf-8")


def test_a_prose_source_is_not_treated_as_a_missing_file(env):
    """'the Q3 board pack' is a real source and is not checkable here. Saying nothing about it
    beats inventing a verdict."""
    put_note(env, id="c-4", title="CONCLUSION — renewals slipped in Q3", type="episodic",
             tenant="work", sensitivity="internal", egress="cloud-ok", status="committed",
             tags=["journal", "conclusion"],
             body="renewals slipped\n\nsource: the Q3 finance pack, page 4\n")
    run(env, "sweep", "--no-heartbeat", expect=0)
    assert '"broken_source_conclusions": 0' in pathlib.Path(
        env["_D"], "state", "status.json").read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────── status is honest about itself

def test_status_before_any_sweep_says_so(env):
    r = run(env, "status", expect=0)
    assert "NEVER RUN" in r.stdout


def test_status_alarms_when_the_sweep_has_stopped_running(env):
    """A detector that silently stopped looks exactly like a clean store."""
    run(env, "sweep", "--no-heartbeat", expect=0)
    sp = pathlib.Path(env["_D"], "state", "status.json")
    data = json.loads(sp.read_text(encoding="utf-8"))
    data["last_run"] = _iso(_old(40))
    sp.write_text(json.dumps(data), encoding="utf-8")
    r = run(env, "status", expect=0)
    assert "STALE" in r.stdout


def test_the_heartbeat_is_written_into_the_store(env):
    """The status file is local and proves nothing to a later session; the note does."""
    run(env, "sweep", expect=0)
    got = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(KIT / 'core' / '01-memory')!r});\n"
         "import agentos_store as s; print(bool(s.get('hygiene-sweep-heartbeat')))"],
        capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
    assert got.stdout.strip() == "True", got.stderr


# ─────────────────────────────────────────────────────────── the classifiers, directly

@pytest.fixture(scope="module")
def mod():
    os.environ.setdefault("HARNESS_HOME", str(KIT))
    sys.path.insert(0, str(KIT / "core" / "06-hygiene"))
    import hygiene
    return hygiene


def test_is_mistag_needs_both_halves(mod):
    assert mod.is_mistag("SHIPPED — the export", "It went out.")
    assert not mod.is_mistag("SHIPPED — the export", "ONE ACTION: still need to document it.")
    assert not mod.is_mistag("Rotate the warehouse key", "It is done.")


def test_action_tokens_separates_identifiers_from_english(mod):
    strong, plain = mod.action_tokens(
        "Add currency to etl/dim_customer.sql", "ONE ACTION: add the currency column")
    assert "etl/dim_customer.sql" in strong
    assert "currency" in plain
    assert "add" not in plain and "the" not in plain     # generic words corroborate nothing


def test_status_survives_a_store_it_cannot_import(env):
    """`status` runs from a SessionStart hook on every session. A hook that dies because a store
    path moved is a hook you turn off, and then the alarm is gone too."""
    r = subprocess.run(
        [sys.executable, str(HYGIENE), "status"], capture_output=True, text=True,
        env={**env, "HARNESS_HOME": "/nonexistent-harness"}, timeout=60, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "hygiene" in r.stdout.lower()


def test_sweep_refuses_clearly_when_the_store_is_missing(env):
    """The other half: a command that genuinely needs the store must say so, not half-run."""
    r = subprocess.run(
        [sys.executable, str(HYGIENE), "sweep", "--no-heartbeat"], capture_output=True, text=True,
        env={**env, "HARNESS_HOME": "/nonexistent-harness"}, timeout=60, encoding="utf-8", errors="replace")
    assert r.returncode != 0
    assert "cannot import the memory store" in (r.stderr + r.stdout)
