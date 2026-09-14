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
import time

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
                          capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")


def declare(env, **kw):
    q = pathlib.Path(env["_D"]) / "proj/.claude/state/continuation/s1.json"
    q.write_text(json.dumps({"items": [], **kw}), encoding="utf-8")


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
#
# These used to read the bash Stop guard's text and look for `|| true`, `>/dev/null`, `timeout`
# and a trailing `&`. That proved the script SAID the right things, and it only meant anything
# where bash runs. The guard is Python now, so they run it: a journal that records it was called,
# one that hangs, one that crashes — and a guard that still blocks, so "exit 0, no output" cannot
# pass for a guard that has stopped deciding anything.
GUARD = KIT / "core" / "02-session" / "stop_guard.py"
STOP_INPUT = {"session_id": "s1", "transcript_path": "", "stop_hook_active": False}


def _fake_journal(env, body):
    p = pathlib.Path(env["_D"]) / "fake_journal.py"
    p.write_text(body, encoding="utf-8")
    return p


def _stop(env, journal):
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(STOP_INPUT),
                       capture_output=True, text=True, encoding="utf-8", timeout=60,
                       env={**env, "HARNESS_STOP_GUARD_JOURNAL": str(journal)})
    return r, time.monotonic() - t0


def test_the_stop_guard_rolls_the_journal(env):
    """The placement IS the argument: capture as a side effect of a gate that already runs."""
    marker = pathlib.Path(env["_D"]) / "rolled.txt"
    fake = _fake_journal(env, "import pathlib, sys\n"
                              f"pathlib.Path({str(marker)!r}).write_text("
                              "' '.join(sys.argv[1:]), encoding='utf-8')\n")
    r, _ = _stop(env, fake)
    assert r.returncode == 0, r.stderr
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not (marker.exists() and marker.read_text(encoding="utf-8")):
        time.sleep(0.1)
    assert marker.exists(), "the Stop guard never ran the journal"
    assert marker.read_text(encoding="utf-8") == "roll --quiet"


def test_a_hanging_journal_cannot_hold_up_the_stop(env):
    r, took = _stop(env, _fake_journal(env, "import time\ntime.sleep(25)\n"))
    assert r.returncode == 0, r.stderr
    assert took < 15, f"the stop waited {took:.1f}s on a hanging journal"


def test_a_crashing_journal_cannot_change_the_decision(env):
    r, _ = _stop(env, _fake_journal(env, "import sys\nraise SystemExit('journal is broken')\n"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", "nothing is outstanding, so the stop must be allowed"


def test_the_guard_still_blocks_while_the_journal_is_broken(env):
    declare(env, items=[{"id": "w1", "text": "finish the migration note"}])
    r, _ = _stop(env, _fake_journal(env, "import sys\nraise SystemExit(3)\n"))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["decision"] == "block"


def test_the_journal_roll_is_time_limited():
    """The detached child is what enforces the limit, so test it directly."""
    sys.path.insert(0, str(GUARD.parent))
    import stop_guard
    with tempfile.TemporaryDirectory() as d:
        slow = pathlib.Path(d) / "slow.py"
        slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        t0 = time.monotonic()
        assert stop_guard.roll_journal(str(slow), timeout=1) == 0
        assert time.monotonic() - t0 < 10


@pytest.mark.skipif(os.name == "nt", reason="the bash shim only exists for POSIX installs")
def test_the_old_bash_entry_point_still_works(env):
    """Existing installs point settings.json at the .sh; it must keep deciding the same way."""
    declare(env, items=[{"id": "w1", "text": "unfinished"}])
    shim = KIT / "core" / "02-session" / "unfinished-work-stop-guard.sh"
    r = subprocess.run(["bash", str(shim)], input=json.dumps(STOP_INPUT), capture_output=True,
                       text=True, encoding="utf-8", timeout=60,
                       env={**env, "HARNESS_STOP_GUARD_JOURNAL": str(pathlib.Path(env["_D"]) / "none.py")})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["decision"] == "block"
