#!/usr/bin/env python3
"""Tests for the lane board.

The properties under test are not "a lane can be claimed". They are the four claims the
component makes that would make it WORSE than nothing if they were false:

  * a lock that is reported is a lock that was actually written (read-back);
  * a claim is refused when someone else is already working the same surfaces;
  * an approval is recorded and an unattended agent cannot manufacture one;
  * "shipped" is closed on hard, re-checkable evidence only — and an inability to check is
    reported as such, never rendered as a clean board or as a conflict.

A board that quietly gets any of those wrong reports coordination that is not happening, which
is strictly worse than having no board at all.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

KIT = pathlib.Path(__file__).resolve().parents[2]
LANES = KIT / "core" / "05-lanes" / "lanes.py"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for t in ("work", "meta"):
            (d / t).mkdir()
            _git(d / t, "init", "-q")
            _git(d / t, "config", "user.email", "a@b.c")
            _git(d / t, "config", "user.name", "t")
        e = {**os.environ, "HARNESS_HOME": str(KIT),
             "HARNESS_WORK_STORE": str(d / "work"), "HARNESS_META_STORE": str(d / "meta"),
             "HARNESS_LANES_CONFIG": str(d / "lanes.json"), "_D": str(d)}
        e.pop("HARNESS_UNATTENDED", None)
        e.pop("CLAUDE_CODE_SESSION_ID", None)
        yield e


def L(env, *args):
    return subprocess.run([sys.executable, str(LANES), *args],
                          capture_output=True, text=True, env=env)


def configure(env, **kw):
    pathlib.Path(env["HARNESS_LANES_CONFIG"]).write_text(json.dumps(kw))


def make_repo(env, merged=("feature/shipped",), unmerged=("feature/wip",)):
    """A repo with real merged and unmerged branches — the ground truth reconcile reads."""
    r = pathlib.Path(env["_D"]) / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "a@b.c")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("1")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    for b in merged:
        _git(r, "checkout", "-qb", b)
        (r / "a.txt").write_text(b)
        _git(r, "commit", "-qam", b)
        _git(r, "checkout", "-q", "main")
        _git(r, "merge", "-q", "--no-ff", b, "-m", f"merge {b}")
    for b in unmerged:
        _git(r, "checkout", "-qb", b)
        (r / "a.txt").write_text(b)
        _git(r, "commit", "-qam", b)
        _git(r, "checkout", "-q", "main")
    configure(env, repo=str(r), trunk="main", pr_cli=None)
    return r


def lane(env, key="L1", **kw):
    return L(env, "lane-add", key, "--title", kw.get("title", f"lane {key}"),
             "--rank", str(kw.get("rank", 1)))


# ─────────────────────────────────────────────────────────────────── the lock is a real lock

def test_a_claim_is_read_back_before_it_is_reported(env):
    lane(env)
    r = L(env, "claim", "L1", "--task", "t", "--surfaces", "etl/dim_customer.sql")
    assert r.returncode == 0, r.stderr
    assert "read-back OK" in r.stdout
    # and the board agrees, which is the only claim that matters to a second agent
    assert "🔒" in L(env, "board").stdout


def test_a_held_lane_refuses_a_second_claim(env):
    lane(env)
    L(env, "claim", "L1", "--task", "one", "--surfaces", "etl/a.sql")
    r = L(env, "claim", "L1", "--task", "two", "--surfaces", "etl/b.sql")
    assert r.returncode != 0
    assert "HELD" in r.stderr


def test_an_expired_lease_frees_the_lane(env):
    """A dead agent must not hold a lane forever. A board people learn to ignore is not a board."""
    lane(env)
    L(env, "claim", "L1", "--task", "one", "--surfaces", "etl/a.sql", "--hours", "-1")
    r = L(env, "claim", "L1", "--task", "two", "--surfaces", "etl/b.sql")
    assert r.returncode == 0, r.stderr
    assert "lease expired" in r.stdout


def test_overlapping_surfaces_refuse_across_lanes(env):
    lane(env, "L1")
    lane(env, "L2", rank=2)
    L(env, "claim", "L1", "--task", "one", "--surfaces", "etl/dim_customer.sql")
    r = L(env, "claim", "L2", "--task", "two", "--surfaces", "etl/dim_customer.sql, docs/x.md")
    assert r.returncode != 0
    assert "surface collision" in r.stderr


def test_a_ubiquitous_directory_alone_is_not_a_collision(env):
    """Every lane touches `docs/`. Refusing on that would fire constantly and get switched off
    within a week, taking the real collisions with it."""
    lane(env, "L1")
    lane(env, "L2", rank=2)
    L(env, "claim", "L1", "--task", "one", "--surfaces", "docs, etl/a.sql")
    r = L(env, "claim", "L2", "--task", "two", "--surfaces", "docs, etl/b.sql")
    assert r.returncode == 0, r.stderr


def test_force_records_a_preempt_rather_than_blocking(env):
    lane(env)
    L(env, "claim", "L1", "--task", "one", "--surfaces", "etl/a.sql")
    r = L(env, "claim", "L1", "--task", "two", "--surfaces", "etl/a.sql", "--force")
    assert r.returncode == 0, r.stderr


def test_claiming_an_undefined_lane_is_refused_with_the_command_that_fixes_it(env):
    r = L(env, "claim", "L9", "--task", "t", "--surfaces", "etl/a.sql")
    assert r.returncode != 0
    assert "lane-add L9" in r.stderr


def test_a_held_but_undefined_lane_still_renders(env):
    """Paperwork lagging reality must never make live work invisible."""
    L(env, "claim", "L9", "--task", "t", "--surfaces", "etl/a.sql", "--force")
    out = L(env, "board").stdout
    assert "L9" in out and "no lane definition" in out


# ────────────────────────────────────────────────────────────────── the approval gate holds

def test_approval_requires_prefixed_evidence(env):
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "agent", "--tier", "yellow")
    r = L(env, "approve", "l1-x", "--evidence", "the operator said yes")
    assert r.returncode != 0
    assert "recorded, never inferred" in r.stderr


def test_an_unattended_session_cannot_approve(env):
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "agent", "--tier", "yellow")
    r = L({**env, "HARNESS_UNATTENDED": "1"}, "approve", "l1-x",
          "--evidence", "quote: approved verbally")
    assert r.returncode != 0
    assert "unattended" in r.stderr


def test_your_own_add_is_the_approval(env):
    lane(env)
    r = L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    assert "status approved" in r.stdout


def test_an_agent_item_above_the_precleared_tier_waits(env):
    lane(env)
    green = L(env, "add", "L1", "--title", "g", "--what", "w", "--origin", "agent",
              "--tier", "green")
    red = L(env, "add", "L1", "--title", "r", "--what", "w", "--origin", "agent", "--tier", "red")
    assert "status approved" in green.stdout
    assert "status proposed" in red.stdout


def test_pre_clearing_nothing_makes_every_agent_item_wait(env):
    configure(env, agent_auto_approve_tiers=[])
    lane(env)
    r = L(env, "add", "L1", "--title", "g", "--what", "w", "--origin", "agent", "--tier", "green")
    assert "status proposed" in r.stdout


def test_editing_the_scope_invalidates_the_approval(env):
    """An approval covers what was approved. Rewritten work must not inherit an old yes."""
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "original scope", "--origin", "me")
    r = L(env, "item-set", "l1-x", "--what", "a completely different job")
    assert "no longer covers it" in r.stdout
    assert "SCOPE CHANGED" in L(env, "queue").stdout
    assert L(env, "pick").returncode != 0        # and it is not pickable until re-approved


def test_re_adding_an_approved_item_does_not_carry_the_approval(env):
    """A re-add is a rewrite. A silently-revoked yes looks exactly like a lost one, and the two
    call for opposite responses — so it is announced."""
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "original scope", "--origin", "me")
    r = L(env, "add", "L1", "--title", "x", "--what", "different job", "--origin", "agent",
          "--tier", "red")
    assert "REPLACED" in r.stdout
    assert "did not carry over" in r.stdout
    assert "status proposed" in r.stdout


def test_an_expired_approval_is_flagged_and_not_pickable(env):
    configure(env, series=[{"key": "L", "label": "line", "contended": True, "ttl_days": -1}])
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    assert "STALE approval" in L(env, "queue").stdout
    assert L(env, "pick").returncode != 0


# ──────────────────────────────────────────────────────────────── incidents are not decisions

def test_an_incident_requires_verbatim_evidence(env):
    lane(env)
    r = L(env, "add", "L1", "--title", "load failed", "--what", "w", "--origin", "monitor",
          "--kind", "incident", "--severity", "crit")
    assert r.returncode != 0
    assert "raw-evidence" in r.stderr


def test_an_incident_never_enters_the_approval_queue(env):
    lane(env)
    L(env, "add", "L1", "--title", "load failed", "--what", "the nightly load died",
      "--origin", "monitor", "--kind", "incident", "--severity", "crit",
      "--raw-evidence", "dag FAILED 03:12")
    out = L(env, "needs-you").stdout
    assert "Open incidents (1)" in out
    assert "Needs your yes" not in out
    r = L(env, "approve", "load-failed", "--evidence", "operator-add: ok")
    assert r.returncode != 0
    assert "incident" in r.stderr


def test_an_incident_closes_on_health_not_on_absence(env):
    lane(env)
    L(env, "add", "L1", "--title", "load failed", "--what", "w", "--origin", "monitor",
      "--kind", "incident", "--raw-evidence", "dag FAILED")
    assert L(env, "resolve", "load-failed", "--raw-evidence", "").returncode != 0
    r = L(env, "resolve", "load-failed", "--raw-evidence", "dag SUCCESS 03:04, three nights")
    assert r.returncode == 0 and "read-back OK" in r.stdout


def test_triage_can_annotate_but_never_escalate(env):
    lane(env)
    L(env, "add", "L1", "--title", "load failed", "--what", "w", "--origin", "monitor",
      "--kind", "incident", "--severity", "info", "--raw-evidence", "dag FAILED")
    r = L(env, "triage", "load-failed", "--note", "looks like the upstream key change")
    assert r.returncode == 0 and "ADVISORY" in r.stdout
    assert "INFO" in L(env, "needs-you").stdout      # severity untouched by the comment


def test_a_merged_pr_does_not_close_an_incident(env):
    """Shipping code does not make a system healthy. Only the check reporting health closes it."""
    make_repo(env)
    lane(env)
    L(env, "add", "L1", "--title", "load failed", "--what", "w", "--origin", "monitor",
      "--kind", "incident", "--raw-evidence", "dag FAILED")
    L(env, "item-set", "load-failed", "--artifact", "branch feature/shipped")
    out = L(env, "reconcile", "--apply").stdout
    assert "load-failed" not in out


# ───────────────────────────────────────────────────────────── shipped means provably shipped

def test_a_merged_branch_closes_an_item(env):
    make_repo(env)
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    L(env, "item-set", "l1-x", "--artifact", "branch feature/shipped")
    out = L(env, "reconcile", "--apply").stdout
    assert "is merged into main" in out and "closed" in out


def test_an_unmerged_branch_does_not(env):
    make_repo(env)
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    L(env, "item-set", "l1-x", "--artifact", "branch feature/wip")
    out = L(env, "reconcile", "--apply").stdout
    assert "Nothing to close" in out


def test_slug_similarity_is_surfaced_and_never_closed(env):
    """The rule the whole component turns on: an inference must not masquerade as a fact."""
    make_repo(env)
    lane(env)
    L(env, "add", "L1", "--title", "rebuild customer dimension table", "--what", "w",
      "--origin", "me")
    L(env, "item-set", "rebuild-customer-dimension", "--artifact", "branch feature/shipped")
    L(env, "reconcile", "--apply")
    L(env, "add", "L1", "--title", "rebuild customer dimension table again", "--what", "w",
      "--origin", "me")
    out = L(env, "reconcile", "--apply").stdout
    assert "Possible duplicates" in out
    assert "NOT auto-closed" in out
    assert "[approved]" in out          # still open; only surfaced


def test_being_unable_to_check_is_reported_not_rendered_as_clean(env):
    """An empty result from a check that never ran is indistinguishable from a clean board.
    Saying so is the entire difference between a gate and a decoration."""
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    L(env, "item-set", "l1-x", "--artifact", "PR #412")
    out = L(env, "reconcile").stdout
    assert "SKIPPED" in out and "no repo configured" in out
    assert "needs-you" and "NOT CHECKED" in L(env, "needs-you").stdout


def test_being_unable_to_check_does_not_block_a_pick(env):
    """The bug this pins: returning 'I could not check' as a conflict made `pick` refuse forever
    on any host without a PR CLI, while also calling a non-existent conflict a conflict."""
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    r = L(env, "pick")
    assert r.returncode == 0, r.stderr
    assert "NOT RUN" in r.stdout
    assert "PICKED" in r.stdout


def test_closing_by_hand_still_needs_evidence(env):
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    r = L(env, "item-set", "l1-x", "--status", "done")
    assert r.returncode != 0
    assert "never asserted" in r.stderr
    assert L(env, "item-set", "l1-x", "--status", "done",
             "--evidence", "cmd: verified in prod").returncode == 0


def test_item_set_cannot_approve(env):
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "agent", "--tier", "red")
    r = L(env, "item-set", "l1-x", "--status", "approved")
    assert r.returncode != 0


# ────────────────────────────────────────────────────────────────────── the work-in-flight caps

def test_one_item_in_flight_per_lane(env):
    lane(env)
    L(env, "add", "L1", "--title", "one", "--what", "w", "--origin", "me")
    L(env, "add", "L1", "--title", "two", "--what", "w", "--origin", "me")
    assert L(env, "pick").returncode == 0
    assert L(env, "pick").returncode != 0


def test_pick_skips_a_parked_lane(env):
    lane(env, "L1")
    lane(env, "L2", rank=2)
    L(env, "lane-set", "L1", "--disposition", "parked")
    L(env, "add", "L1", "--title", "parked work", "--what", "w", "--origin", "me")
    L(env, "add", "L2", "--title", "live work", "--what", "w", "--origin", "me")
    out = L(env, "pick").stdout
    assert "l2-live-work" in out


def test_an_item_in_progress_with_no_live_lock_is_surfaced(env):
    """A vanished agent is the anomaly the board exists to catch."""
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    L(env, "pick")
    L(env, "release", "L1")
    out = L(env, "board").stdout
    assert "nobody holding the lane" in out


# ────────────────────────────────────────────────────────────────────── the operator surface

def test_needs_you_is_empty_when_nothing_is_gated(env):
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me")
    assert "Nothing is gated on you" in L(env, "needs-you").stdout


def test_every_block_carries_the_steps_that_clear_it(env):
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me",
      "--blocked-on", "you must confirm the retention policy",
      "--accel", "open the policy doc, sign section 4")
    out = L(env, "needs-you").stdout
    assert "How to do it:** open the policy doc" in out


def test_a_block_with_no_steps_says_so_rather_than_going_quiet(env):
    lane(env)
    L(env, "add", "L1", "--title", "x", "--what", "w", "--origin", "me",
      "--blocked-on", "you must confirm the retention policy")
    assert "itself a defect" in L(env, "needs-you").stdout


def test_the_budget_line_appears_only_when_over(env):
    configure(env, budget={"awaiting_operator": 1, "open_followups": 150})
    lane(env)
    for i in range(3):
        L(env, "add", "L1", "--title", f"item {i}", "--what", "w", "--origin", "agent",
          "--tier", "red")
    assert "Over budget" in L(env, "needs-you").stdout


def test_config_reports_what_is_actually_in_force(env):
    make_repo(env)
    out = L(env, "config").stdout
    assert "repo" in out and str(pathlib.Path(env["_D"]) / "repo") in out
    assert "trunk       : main" in out
