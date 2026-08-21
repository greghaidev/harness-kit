#!/usr/bin/env python3
"""Tests for the journal — the capture that does not depend on remembering to capture.

The property under test is not "notes can be written". It is that the DAY ROLL-UP is
derived, idempotent, and safe to call from a hook on every stop — because that placement
is the entire argument. A roll-up that corrupted itself on a second call, or that could
fail a stop decision, would have to be moved back to the human-remembered rung.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

KIT = pathlib.Path(__file__).resolve().parents[2]
JOURNAL = KIT / "core" / "04-journal" / "journal.py"


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for t in ("work", "meta"):
            (d / t).mkdir()
            subprocess.run(["git", "init", "-q"], cwd=d / t, check=True)
            subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=d / t, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=d / t, check=True)
        (d / "proj" / ".claude" / "state" / "continuation").mkdir(parents=True)
        yield {**os.environ, "HARNESS_HOME": str(KIT),
               "HARNESS_WORK_STORE": str(d / "work"),
               "HARNESS_META_STORE": str(d / "meta"),
               "CLAUDE_PROJECT_DIR": str(d / "proj"), "_D": str(d)}


def j(env, *args):
    return subprocess.run([sys.executable, str(JOURNAL), *args],
                          capture_output=True, text=True, env=env)


def declare(env, **kw):
    q = pathlib.Path(env["_D"]) / "proj/.claude/state/continuation/s1.json"
    q.write_text(json.dumps({"items": [], **kw}))


# ---------------------------------------------------------------- the four hand kinds
@pytest.mark.parametrize("kind", ["decision", "friction", "idea", "question"])
def test_each_hand_kind_records(kind, env):
    r = j(env, "note", f"a {kind} worth keeping", "--kind", kind)
    assert r.returncode == 0, r.stderr
    assert kind in r.stdout


def test_a_conclusion_cannot_be_written_by_hand(env):
    """Conclusions come from the terminal declaration, not from the journal — one channel."""
    r = j(env, "note", "x", "--kind", "conclusion")
    assert r.returncode != 0


def test_a_decision_without_a_reason_is_recorded_but_flagged(env):
    r = j(env, "note", "chose A over B", "--kind", "decision")
    assert r.returncode == 0
    assert "no --why" in r.stdout


# ---------------------------------------------------------------- the roll-up
def test_roll_is_idempotent(env):
    """It runs on every stop. Twice must equal once, or it cannot live in a hook."""
    j(env, "note", "a decision", "--kind", "decision")
    declare(env, claims=[{"statement": "n is 4", "source": "q.sql"}])
    a = j(env, "roll")
    b = j(env, "roll")
    assert a.returncode == b.returncode == 0
    body_a = a.stdout.split("# ", 1)[1]
    body_b = b.stdout.split("# ", 1)[1]
    assert body_a == body_b
    assert body_a.count("a decision") == 1


def test_roll_pulls_conclusions_from_terminal_declarations(env):
    declare(env, claims=[{"statement": "churn is 4.1%", "source": "warehouse.fct",
                          "does_not_establish": "that the fix caused it"}])
    out = j(env, "roll").stdout
    assert "churn is 4.1%" in out
    assert "warehouse.fct" in out
    assert "does not establish" in out


def test_roll_marks_an_unsourced_conclusion(env):
    declare(env, claims=[{"statement": "revenue is up"}])
    assert "UNSOURCED" in j(env, "roll").stdout


def test_roll_captures_limits_discards_and_holds(env):
    declare(env, limits=[{"what": "cannot see October"}],
            discards=[{"why": "cohort too small"}],
            hold={"waiting_on": "which definition of active"})
    out = j(env, "roll").stdout
    assert "cannot see October" in out
    assert "cohort too small" in out
    assert "which definition of active" in out


def test_roll_on_an_empty_day_is_quiet_and_succeeds(env):
    r = j(env, "roll", "--quiet")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_day_reports_honestly_when_nothing_has_been_rolled(env):
    r = j(env, "day")
    assert r.returncode == 1
    assert "no roll-up" in r.stdout


def test_day_reads_back_what_roll_wrote(env):
    j(env, "note", "the schema tool drops defaults", "--kind", "friction")
    j(env, "roll", "--quiet")
    out = j(env, "day").stdout
    assert "the schema tool drops defaults" in out


def test_recent_lists_rollups(env):
    j(env, "note", "x", "--kind", "idea")
    j(env, "roll", "--quiet")
    assert j(env, "recent", "5").returncode == 0


def test_a_bad_date_is_refused_rather_than_guessed(env):
    assert j(env, "roll", "--date", "yesterday").returncode != 0


# ---------------------------------------------------------------- the hook placement
def test_the_stop_guard_invokes_the_journal():
    """The placement IS the argument: capture as a side effect of a gate that already runs."""
    body = (KIT / "core/02-session/unfinished-work-stop-guard.sh").read_text()
    assert "04-journal/journal.py" in body
    assert "roll" in body


def test_the_stop_guard_cannot_be_broken_by_the_journal():
    """Backgrounded, timed out, output discarded, `|| true`. A broken journal must never
    be able to affect whether a turn is allowed to end."""
    body = (KIT / "core/02-session/unfinished-work-stop-guard.sh").read_text()
    # The path is a shell variable at the call site, so match the invocation, not the
    # literal filename — the earlier probe matched only the assignment line and then
    # indexed an empty list.
    line = [l for l in body.split("\n") if "$JOURNAL" in l and " roll" in l][0]
    assert "|| true" in line
    assert ">/dev/null" in line
    assert "timeout" in line
    assert line.rstrip().endswith("&")
