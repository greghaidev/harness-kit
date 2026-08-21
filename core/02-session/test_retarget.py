#!/usr/bin/env python3
"""Tests for THE RETARGET — the one behaviour change this port makes to the Stop guard.

The home guard asks "did this session change something." For a communicating analyst the
residue risk is not a dropped task, it is an unrecorded conclusion: a session that analyzes,
concludes, and pastes the number into someone else's deck has an EMPTY queue and a CLEAN
worktree, ends "cleanly", and leaves its only durable product with no provenance.

Same invariant. Different state.
"""
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import unfinished_work_lib as u  # noqa: E402


@pytest.fixture
def proj():
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / ".claude/state/continuation").mkdir(parents=True)
        yield d


_n = [0]


def transcript(tmpdir, calls):
    # Unique per call. A fixed filename made three "different" transcripts in one test
    # the same file, last write winning — the probe was not measuring what it named.
    _n[0] += 1
    p = pathlib.Path(tmpdir) / f"t{_n[0]}.jsonl"
    lines = []
    for name, arg in calls:
        key = "command" if name == "Bash" else "file_path"
        lines.append(json.dumps({"type": "assistant", "message": {
            "role": "assistant", "content": [
                {"type": "tool_use", "name": name, "input": {key: arg}}]}}))
    p.write_text("\n".join(lines))
    return str(p)


def cont(proj, *args):
    return subprocess.run(
        [sys.executable, str(HERE / "continuation.py"), "--session", "s1", *args],
        capture_output=True, text=True,
        env={"CLAUDE_PROJECT_DIR": proj, "PATH": "/usr/bin:/bin"})


def stop(proj, tp=""):
    return u.decide_stop({"session_id": "s1", "transcript_path": tp,
                          "stop_hook_active": False}, project_dir=proj)


# --------------------------------------------------- the analysis predicate
@pytest.mark.parametrize("cmd", [
    "psql -c 'select count(*) from orders'",
    "duckdb -c 'select 1'",
    "bq query --nouse_legacy_sql 'select 1'",
    "dbt run --select churn",
    "python3 -c \"import x\" data/extract.csv",
])
def test_running_a_query_counts_as_producing_something(cmd, proj):
    assert u.session_analyzed(transcript(proj, [("Bash", cmd)])) is True


@pytest.mark.parametrize("cmd", [
    "git status", "ls -la", "grep -rn foo src/", "cat README.md",
    "gh pr list", "pytest -q",
])
def test_read_only_investigation_is_not_analysis(cmd, proj):
    """The 76%-waste guard. Reading code is not producing a number."""
    assert u.session_analyzed(transcript(proj, [("Bash", cmd)])) is False


def test_opening_a_dataset_counts(proj):
    assert u.session_analyzed(transcript(proj, [("Read", "/data/q3.parquet")])) is True


def test_session_produced_is_the_union_of_changed_and_established(proj):
    edit = transcript(proj, [("Write", "/x.py")])
    query = transcript(proj, [("Bash", "psql -c 'select 1'")])
    neither = transcript(proj, [("Bash", "git status")])
    assert u.session_produced(edit) is True
    assert u.session_produced(query) is True
    assert u.session_produced(neither) is False


# --------------------------------------------------- the guard's behaviour
def test_THE_CASE_analysis_with_no_declaration_is_blocked(proj):
    """The exact scenario: queue empty, worktree clean, a number was produced."""
    d = stop(proj, transcript(proj, [("Bash", "psql -c 'select avg(x) from t'")]))
    assert d["block"] is True
    assert d["reason"] == "no_completion_declaration"
    assert "claim" in d["message"]


def test_a_recorded_claim_ends_the_turn(proj):
    cont(proj, "claim", "churn is 4.1%", "--source", "warehouse.fct_subs")
    d = stop(proj, transcript(proj, [("Bash", "psql -c 'select 1'")]))
    assert d["block"] is False
    assert d["reason"] == "declared_claim"


def test_a_bounded_i_do_not_know_ends_the_turn(proj):
    """The dissent that survived review: an analyst must sometimes end on a limit."""
    cont(proj, "limit", "whether the backfill covers October")
    d = stop(proj, transcript(proj, [("Bash", "psql -c 'select 1'")]))
    assert d["block"] is False
    assert d["reason"] == "declared_limit"


def test_a_discard_ends_the_turn(proj):
    cont(proj, "discard", "the cohort was too small to say anything")
    d = stop(proj, transcript(proj, [("Bash", "psql -c 'select 1'")]))
    assert d["block"] is False
    assert d["reason"] == "declared_discard"


def test_a_hold_still_reports_as_a_hold_not_as_a_claim(proj):
    cont(proj, "hold", "which definition of active user to use")
    d = stop(proj, transcript(proj, [("Bash", "psql -c 'select 1'")]))
    assert d["reason"] == "held_on_operator"


def test_read_only_session_with_no_declaration_still_stops_clean(proj):
    """The retarget must not reintroduce the waste the home guard measured away."""
    d = stop(proj, transcript(proj, [("Bash", "git log"), ("Read", "/src/a.py")]))
    assert d["block"] is False
    assert d["reason"] == "no_outstanding_work"


def test_an_unsourced_claim_is_recorded_but_names_the_gap(proj):
    out = cont(proj, "claim", "revenue is up")
    assert "no --source" in out.stdout
    assert stop(proj)["block"] is False


def test_claim_closes_open_items_so_the_guard_sees_one_answer(proj):
    cont(proj, "add", "some work")
    cont(proj, "claim", "the number is 12", "--source", "q.sql")
    assert stop(proj)["block"] is False


def test_does_not_establish_is_persisted(proj):
    cont(proj, "claim", "churn fell", "--source", "q.sql",
         "--does-not-establish", "that the fix caused it")
    data = json.loads((pathlib.Path(proj) / ".claude/state/continuation/s1.json").read_text())
    assert data["claims"][0]["does_not_establish"] == "that the fix caused it"
