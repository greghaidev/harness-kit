#!/usr/bin/env python3
"""unfinished_work_lib.py — Stop-hook guard against ending a turn with work outstanding.

WHY THIS EXISTS (the operator, 2026-07-26, after the fourth occurrence in one session):
an agent finishes a coherent chunk, writes "Continuing" or "Next is X", and ends the
turn. The promise reads like continuation, so nothing looks wrong — but no work
happens until the operator notices and asks "is it running?". It happened four times
in a single session on the same program, each time after a merged PR.

The operator's instruction was explicit: *"This needs permanently fixed. Don't fix it in this
session. Fix it forever."* Prose had already failed twice on an adjacent rule (the
UAT-only agreement was written 2026-07-12, forgotten, re-stated angrily 2026-07-26,
and written down again the same day). So this is a mechanism, not a reminder.

WHAT IT CANNOT DO, and why the obvious design is wrong
------------------------------------------------------
The natural trigger is "the final message promised more work" — detect "Continuing",
"I'll keep going", "Next is X". That cannot work here: **at Stop time the assistant's
final message is not yet flushed to the transcript.** This repo already proved it the
hard way in session_codename_lib (the codename backstop fired on every stop of a
session that stated its codename every single time). So the closing words are
invisible, and any guard reading them is guessing.

The trigger therefore has to be STATE a hook can observe at Stop time.

BLOCK-UNTIL-CLEAR, not block-once
---------------------------------
session_codename_lib and session_log_lib both block ONCE per session, correctly:
neither can observe success, so nagging would be pure alarm fatigue. This guard is
the opposite case — its conditions are directly observable and directly clearable:

    the continuation queue empties        -> signal gone
    the dirty worktree gets committed     -> signal gone

Because success is observable, blocking once would be the wrong shape: one nudge is
exactly what failed four times. It blocks every stop while work is genuinely
outstanding, and stops blocking the moment the work is done — which is the only
behaviour that makes "keep going" true by construction rather than by memory.

LOOP SAFETY
-----------
Three independent guards, because a Stop hook that hard-loops is worse than the
problem it solves:
  1. `stop_hook_active` short-circuits to allow — non-negotiable harness contract; a
     Stop hook re-fires within one stop event and must never spin inside it.
  2. A consecutive-block counter per session. If the signal set is byte-identical
     across MAX_CONSECUTIVE_BLOCKS stops, the agent is stuck rather than idle, so the
     guard steps aside loudly instead of trapping the session.
  3. Any signal that cannot be computed (git missing, unreadable state) degrades to
     "no signal" — this guard never blocks on its own malfunction.

The operator can always interrupt, and `continuation.py clear` is one command.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

MAX_CONSECUTIVE_BLOCKS = 6

_TEMPLATE = """You are about to end the turn with work still outstanding.

{signals}

The operator's standing instruction is to keep going without being asked — treat "good job,
keep going" as permanently granted. Ending a turn with a promise ("Continuing",
"Next is X") is not continuing; the next thing should be a tool call, not a sentence
about tool calls.

Continue the work now. If it is genuinely finished, clear the queue:
    python3 scripts/continuation.py done <item-id>      # one item
    python3 scripts/continuation.py clear               # all of it
If you are blocked on a decision only you can make, record it and stop — that is a
legitimate reason to end the turn; running out of momentum is not:
    python3 scripts/continuation.py hold "<what you need decided>"
"""


_UNDECLARED_TEMPLATE = """This session produced something and has not said where it stands.

You either changed a file or established a number. Either way this session has a durable
product, and right now it is about to leave with no record of what it was.

That is the failure this guard exists to stop, and it is NOT the same as having an empty
task queue. A session that analyzes data, reaches a conclusion, and pastes that conclusion
into someone else's deck has nothing queued — because nothing IS queued — and its only
durable output walks out the door with no provenance attached. Months later the figure gets
quoted back at you and the harness that watched this session close "cleanly" holds nothing.

Say where it stands, in one command:

    claim "<the conclusion>" --source "<what it traces to>"
                                   # you established something -> record it
    limit "<what you could not establish>"
                                   # a bounded "I do not know" — a legitimate ending,
                                   # and a better record than silence
    discard "<why>"                # you reached something not worth keeping; say why
    add "<the next piece of work>" # more to do -> then DO it
    hold "<what you need decided>" # someone else's call, not yours
    clear                          # genuinely finished, nothing produced worth recording

`limit` exists because an analyst must sometimes end with an explicitly bounded "I do not
know." Turning every unresolved limit into queued work distorts the record rather than
improving it. Declaring the limit is the honest terminal state; silence is not.

--- the original task-shaped guidance still applies below ---

This session did real work and has not said where it stands.

An empty continuation queue is not the same as "nothing left to do" — it is the shape of
every premature stop so far: a chunk lands, the worktree is clean, nothing is queued, and
the turn ends on a promise. Say which it is, in one command:

    python3 scripts/continuation.py add "<the next piece of work>"   # more to do -> then DO it
    python3 scripts/continuation.py hold "<what you need the operator to decide>"  # their call, not yours
    python3 scripts/continuation.py clear                            # genuinely finished

Pick by what the operator ASKED FOR, not by whether you have momentum left:

  * He asked you to DO something ("run", "fix", "build", "ship", "run this sprint to
    completion") -> the named scope IS the deliverable. `add` and keep going; the next
    thing should be a tool call, not a sentence about tool calls.
  * He asked you to FIND OUT something ("look at", "re-evaluate", "research", "what do
    you think", "should I") -> the FINDINGS are the deliverable. Deliver them and `hold`.
    Assume their risk tolerance is high and he wants to know first; implementing the
    recommendation is a SEPARATE request that he has not made.
  * You hit a big missing design or scope decision -> `hold`. (A tiny out-of-scope item
    is different: just do it and call it out at the PR.)

This guard exists to stop turns ending on a promise. It is NOT authorization to push past
a gate that belongs to the operator — that inversion happened on 2026-07-29, when this message's
"keep going without being asked" line talked a session out of a correctly-posed question
and into implementing an unapproved recommendation.
"""


# ── state locations ────────────────────────────────────────────────────────────────────────
def _project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def queue_path(session_id, project_dir=None):
    base = project_dir or _project_dir()
    return os.path.join(base, ".claude", "state", "continuation", f"{session_id}.json")


def _counter_path(session_id, project_dir=None):
    base = project_dir or _project_dir()
    return os.path.join(base, ".claude", "state", "continuation", f"{session_id}.blocks")


# ── signal 1: the explicit continuation queue ──────────────────────────────────────────────
def open_items(session_id, project_dir=None):
    """Open items from the session's continuation queue. Unreadable/missing -> []."""
    path = queue_path(session_id, project_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, IOError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and not i.get("done")]


# ── signal 2: a dirty worktree this session created ────────────────────────────────────────
#
# Needs NO cooperation from the agent, which is the point: uncommitted TRACKED changes in a
# worktree whose path carries this session id means work was started and never committed.
# That is exactly how CS.1-T4's ~570 lines were nearly lost — complete on disk, never
# committed, found 16 hours later only because someone ran `git status` in each worktree.
#
# Untracked files are deliberately IGNORED: build output, node_modules symlinks and scratch
# files are normal and would make this fire constantly, which is the alarm-fatigue failure
# this repo has already hit twice (governance-file-guard PR #640, the codename nudge).
def dirty_session_worktrees(session_id, project_dir=None, runner=None):
    if not session_id:
        return []
    runner = runner or _run_git
    out = runner(["worktree", "list", "--porcelain"], project_dir)
    if out is None:
        return []
    paths = [ln.split(" ", 1)[1].strip()
             for ln in out.splitlines() if ln.startswith("worktree ")]
    dirty = []
    for path in paths:
        if session_id not in path:
            continue
        status = runner(["-C", path, "status", "--porcelain", "--untracked-files=no"], project_dir)
        if status:
            dirty.append((path, len([ln for ln in status.splitlines() if ln.strip()])))
    return dirty


def _run_git(args, cwd=None):
    try:
        proc = subprocess.run(["git", *args], cwd=cwd or _project_dir(),
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


# ── signal 3: no completion declaration at all ─────────────────────────────────────────────
#
# THE ONE THAT ACTUALLY CATCHES THE ORIGINAL FAILURE (a second lineage verify, 2026-07-26, FAIL).
#
# Signals 1 and 2 both require something to be OUTSTANDING — a queued item, or an uncommitted
# change. Every one of the four real stops had neither: each came immediately after a merged
# PR, so the worktree was clean and no queue had ever been populated. The first version of
# this guard passed all five of its own invariants and would have caught NONE of them.
# a second lineage named it exactly: "the guard can pass every invariant while still allowing the
# agent to stop whenever the queue is empty and the worktree is clean — likely for an agent
# that commits frequently."
#
# So absence of a queue must not read as "nothing to do". A session that did substantive work
# and never said where it stands has not declared completion; it has merely gone quiet, which
# is indistinguishable from the failure. Requiring one positive act — declare the remaining
# work, or assert done — is what makes "keep going" structural instead of remembered.
#
# Gated on the session having actually used tools, so a purely conversational turn (a
# question, an explanation) never trips it.
def _tool_calls(transcript_path):
    """Every (tool_name, bash_command) this session issued, in order. Unreadable -> []."""
    calls = []
    if not transcript_path:
        return calls
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                message = (entry or {}).get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name") or "?"
                        inp = block.get("input") or {}
                        # Bash fills this slot with its command; every other tool fills it
                        # with the path it touched. session_mutated only ever reads the slot
                        # for Bash, so populating it for the rest is additive — and without
                        # it the retarget's "a dataset was opened" branch can never fire,
                        # because the slot it inspects is unconditionally empty.
                        cmd = (inp.get("command") if name == "Bash"
                               else (inp.get("file_path") or inp.get("notebook_path"))) or ""
                        calls.append((name, cmd))
    except (OSError, IOError):
        return []
    return calls


def session_used_tools(transcript_path):
    """True if this session used ANY tool. The ORIGINAL signal-3 predicate.

    Kept — not as the gate, but as the CONTROL arm of the shadow log below. Measuring the
    narrowing against the rule it replaces is the whole reason the narrowing is shippable.
    """
    return bool(_tool_calls(transcript_path))


# Tools that cannot change anything by themselves. Everything not here is judged by name;
# Bash alone is judged by what it actually RAN (see _MUTATING_BASH).
_MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Bash commands that COMMIT or PUBLISH — deliberately narrow and deliberately incomplete.
# Excluded on purpose: bare `python3 scripts/...`, `pytest`, `git log/status/diff/show`,
# `ls`, `cat`, `curl`, `docker ps`, `gh pr list|view|checks`, `ssh ... 'git log'`. Every one
# of those appears constantly in read-only investigation, and catching them is precisely the
# 76%-waste behaviour being removed. Ambiguity resolves to NOT mutated.
_MUTATING_BASH = re.compile(
    r"\bgit\s+(commit|merge|push|rebase|revert|cherry-pick|am|worktree\s+add)\b"
    r"|\bgit\s+reset\s+--hard\b"
    r"|\bgh\s+pr\s+(create|merge)\b"
    r"|\bgh\s+release\s+create\b"
    r"|\bdeploy[_-](staging|prod)\b"
    r"|\brun_migrations\.py\b"
)


# --- THE RETARGET (work port, 2026-08-20) ----------------------------------------
# Deliberately narrow, for the same reason _MUTATING_BASH is narrow: in the original design a wider
# signal-3 fired on 66 of 92 stops and ~76% of those bought nothing. This catches the
# case that actually goes missing for an analyst — a NUMBER WAS PRODUCED — and nothing
# else. Reading code, grepping, and answering from memory are not analysis; they leave
# nothing a stakeholder can quote back six months later.
_ANALYSIS_BASH = re.compile(
    r"\b(psql|duckdb|sqlite3|mysql|clickhouse-client|snowsql|impala-shell)\b"
    r"|\bbq\s+query\b"
    r"|\bdbt\s+(run|test|build|snapshot)\b"
    r"|\bjupyter\s+(nbconvert|execute)\b"
    r"|\bpapermill\b"
)
_DATA_SUFFIXES = (".csv", ".parquet", ".xlsx", ".xls", ".tsv", ".ipynb", ".sql")


def session_analyzed(transcript_path):
    """True if this session PRODUCED A NUMBER — ran a query, or opened a dataset.

    The home guard asks "did this session change something." That is the right question
    for a builder, whose residue risk is a dropped task. It is the wrong question for a
    data architect, whose residue risk is a conclusion that leaves the session with no
    provenance and is quoted back months later.

    Same invariant, different state. A session that analyzes and concludes now has to say
    where it stands — with `claim`, `discard` or `limit` — exactly as a session that edits
    a file does.
    """
    for name, cmd in _tool_calls(transcript_path):
        if name == "Bash" and _ANALYSIS_BASH.search(cmd):
            return True
        if name == "Bash" and any(sfx in cmd for sfx in _DATA_SUFFIXES):
            return True
        if name in ("Read", "NotebookEdit") and any(cmd.endswith(s) for s in _DATA_SUFFIXES):
            return True
    return False


def session_produced(transcript_path):
    """The retargeted signal-3 predicate: changed something, or established something."""
    return session_mutated(transcript_path) or session_analyzed(transcript_path)


def session_mutated(transcript_path):
    """True if this session CHANGED something — the narrowed signal-3 predicate.

    THE MEASUREMENT THAT DROVE THIS (2026-08-03). Signal 3 fired on 66 of 92 blocks and ~76%
    of those bought nothing: the agent had already finished and was made to spend 3-5 turns
    saying so. Every tool-name-based definition the PAB proposed was replayed against the real
    transcripts and every one STILL fired on the canonical false positive — a pure question
    answered with ten read-only Bash calls — because in this repo Bash is the universal tool.
    Hence: Write/Edit-class tool use, or a Bash command that commits/publishes. Nothing else.

    The trade is explicit and is recorded in the shadow log rather than assumed away: signal 3
    gives up its fail-closed posture, while signals 1 (queued items) and 2 (dirty session
    worktree) keep theirs unchanged. The residual gap is "mutated in a way this predicate does
    not recognise AND clean worktree AND empty queue AND never declared" — which the shadow
    log measures instead of arguing about.
    """
    for name, cmd in _tool_calls(transcript_path):
        if name in _MUTATING_TOOLS:
            return True
        if name == "Bash" and _MUTATING_BASH.search(cmd):
            return True
    return False


def shadow_path(project_dir=None):
    base = project_dir or _project_dir()
    return os.path.join(base, ".claude", "state", "continuation", "_shadow.jsonl")


def record_shadow_divergence(session_id, transcript_path, project_dir=None):
    """Log one line where the NARROWED rule stays silent but the OLD rule would have fired.

    Zero behaviour change — this only writes. It converts the narrowing from a leap of faith
    into a measured experiment: after two weeks the false-negative rate is a number, and the
    decision about whether a heavier session-sha check is needed gets made on data. Never
    raises; a broken shadow log must never affect a stop decision.
    """
    path = shadow_path(project_dir)
    counts = {}
    for name, _ in _tool_calls(transcript_path):
        counts[name] = counts.get(name, 0) + 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": session_id,
                "tools": counts,
                "note": "old rule would have blocked; narrowed rule permitted",
            }) + "\n")
    except (OSError, IOError, ValueError, TypeError):
        pass


def has_declaration(session_id, project_dir=None):
    """True once the session has written a queue file at all — open OR all-done. The file's
    existence IS the declaration: 'here is what remains' or 'I asserted done'."""
    return os.path.exists(queue_path(session_id, project_dir))


# ── the third terminal state: waiting on a decision only you can make ─────────────────────
#
# Added 2026-07-29 after this guard inverted. The session delivered a research answer, posed
# the decision to the operator correctly, and was then talked out of stopping by this very guard's
# "keep going without being asked" line — so it implemented an unapproved recommendation.
#
# Root cause: `add` and `clear` were the only two states, and NEITHER describes "delivered;
# the next move is the operator's". `clear` was the closest fit but reads as "all done,
# nothing pending", which loses the ask. So the guard offered a menu with no correct answer
# on it, and the wrong answer was the one it advertised hardest.
#
# A hold is only ever set by the agent explicitly naming what the operator must decide, so it cannot
# become a silent escape hatch: the ask is on the record and outlives the session.
def _recorded_terminal(session_id, project_dir=None):
    """claim / discard / limit — the work port's terminal declarations."""
    try:
        with open(queue_path(session_id, project_dir)) as fh:
            data = json.load(fh)
    except Exception:
        return None
    for k in ("claims", "discards", "limits"):
        if data.get(k):
            return {"kind": k, "entries": data[k]}
    return None


def hold_state(session_id, project_dir=None):
    """The session's recorded hold, or None. Unreadable/missing -> None (never blocks)."""
    try:
        with open(queue_path(session_id, project_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, IOError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    hold = data.get("hold")
    return hold if isinstance(hold, dict) else None


# ── consecutive-block accounting ───────────────────────────────────────────────────────────
def _read_counter(session_id, project_dir=None):
    try:
        with open(_counter_path(session_id, project_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return int(data.get("count", 0)), str(data.get("fingerprint", ""))
    except (OSError, IOError, ValueError, TypeError):
        return 0, ""


def _write_counter(session_id, count, fingerprint, project_dir=None):
    path = _counter_path(session_id, project_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"count": count, "fingerprint": fingerprint}, fh)
    except (OSError, IOError):
        pass


# ── the decision ───────────────────────────────────────────────────────────────────────────
def decide_stop(hook_input, project_dir=None, runner=None):
    """Returns {"block": bool, "message": str|None, "reason": str}.

    Blocks while work is outstanding AND clearable. Never blocks on its own failure.
    """
    if hook_input.get("stop_hook_active"):
        return {"block": False, "message": None, "reason": "stop_hook_active"}

    session_id = hook_input.get("session_id") or ""
    if not session_id:
        return {"block": False, "message": None, "reason": "no_session_id"}

    project_dir = project_dir or hook_input.get("_project_dir") or _project_dir()

    items = open_items(session_id, project_dir)
    dirty = dirty_session_worktrees(session_id, project_dir, runner=runner)
    hold = hold_state(session_id, project_dir)
    # The retarget widens the terminal vocabulary; any recorded declaration ends the turn.
    declared_terminal = hold or _recorded_terminal(session_id, project_dir)

    # A recorded hold is a TERMINAL state — the agent has delivered and named what the operator must
    # decide. It does NOT excuse uncommitted work, though: tracked changes sitting in a
    # worktree are a data-loss risk whoever the next move belongs to (CS.1-T4, ~570 lines),
    # so `dirty` still blocks below and the hold is surfaced in that message rather than
    # silently overriding it.
    if declared_terminal and not items and not dirty:
        _write_counter(session_id, 0, "", project_dir)
        # Name WHICH terminal state ended the turn. "held_on_operator" for a recorded
        # claim would misreport in any later audit of how sessions actually end — and
        # the difference matters: a hold is someone else's move, a claim is a product.
        reason = ("held_on_operator" if hold
                  else f"declared_{declared_terminal['kind'][:-1]}")
        return {"block": False, "message": None, "reason": reason}

    if not items and not dirty:
        transcript = hook_input.get("transcript_path", "")
        undeclared = not has_declaration(session_id, project_dir)
        if undeclared and session_produced(transcript):
            count, previous = _read_counter(session_id, project_dir)
            count = count + 1 if previous == "_undeclared_" else 1
            _write_counter(session_id, count, "_undeclared_", project_dir)
            if count > MAX_CONSECUTIVE_BLOCKS:
                return {"block": False, "message": None, "reason": "max_consecutive_blocks"}
            return {"block": True, "message": _UNDECLARED_TEMPLATE,
                    "reason": "no_completion_declaration"}
        # The narrowed predicate let this stop through where the old one would have blocked.
        # Record it so the false-negative rate is measured, not assumed (see session_mutated).
        if undeclared and session_used_tools(transcript):
            record_shadow_divergence(session_id, transcript, project_dir)
        _write_counter(session_id, 0, "", project_dir)
        return {"block": False, "message": None, "reason": "no_outstanding_work"}

    lines = []
    if items:
        lines.append(f"Continuation queue — {len(items)} open item(s):")
        lines += [f"  [{i.get('id', '?')}] {i.get('title', '(untitled)')}" for i in items]
    for path, count in dirty:
        lines.append(f"Uncommitted work: {count} tracked change(s) in {path}")
    if hold:
        lines.append(
            f"(You are HELD on the operator: {hold.get('waiting_on', '(unspecified)')} — commit or "
            f"stash the work above, then the hold stands and you may stop.)")
    signals = "\n".join(lines)

    fingerprint = signals
    count, previous = _read_counter(session_id, project_dir)
    count = count + 1 if fingerprint == previous else 1
    _write_counter(session_id, count, fingerprint, project_dir)

    if count > MAX_CONSECUTIVE_BLOCKS:
        return {
            "block": False,
            "message": None,
            "reason": "max_consecutive_blocks",
        }

    return {"block": True, "message": _TEMPLATE.format(signals=signals),
            "reason": "outstanding_work"}


def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # malformed input must never block a stop
    if not isinstance(hook_input, dict):
        sys.exit(0)

    decision = decide_stop(hook_input)
    if decision["block"]:
        print(json.dumps({"decision": "block", "reason": decision["message"]}))
    sys.exit(0)


if __name__ == "__main__":
    main()
