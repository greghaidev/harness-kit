#!/usr/bin/env python3
"""continuation.py — the session's own work queue, enforced by a Stop hook.

Declare multi-step work here and `.claude/hooks/unfinished-work-stop-guard.sh` refuses
to let the turn end while items remain open. That is the whole point: continuation
stops depending on the agent remembering to continue.

    python3 scripts/continuation.py add "CS.6 subscription + account" [--id cs6]
    python3 scripts/continuation.py list
    python3 scripts/continuation.py done cs6
    python3 scripts/continuation.py hold "close/split PR #695 — Opus 5 recalibration"
    python3 scripts/continuation.py clear

Session is taken from CLAUDE_CODE_SESSION_ID (what the harness exports), else the legacy
CLAUDE_SESSION_ID, else --session — which is accepted EITHER side of the subcommand. Sprint
codenames make good item ids — one per sprint, closed as each lands.

THREE TERMINAL STATES, not two (the operator, 2026-07-29)
-------------------------------------------------
`add` and `clear` conflated three situations and the Stop hook could not tell them apart,
so it pushed a session straight past an operator gate and into implementing a research
recommendation nobody had approved:

    add "<work>"   more to do              -> DON'T STOP; next thing is a tool call
    hold "<ask>"   delivered; blocked on   -> LEGITIMATE end of turn; the ask is recorded
                   a decision only you       so a later session can see what is still
                   can make                   owes an answer on
    clear          finished, nothing       -> legitimate end of turn
                   pending

`hold` is the state that was missing. The operator's rule:

  * A RESEARCH request ("look at", "re-evaluate", "research", "what do you think",
    "should I") ends in findings + `hold`. Assume the risk tolerance is HIGH and they
    just wants to know something first — do NOT implement the recommendation as part
    of answering it.
  * An ACTION request ("run", "fix", "build", "ship", "run this sprint to completion")
    ends in `clear` and nothing less: the named scope IS the deliverable, so don't stop
    partway through it.
  * Genuinely ambiguous? Ask ONE question UP FRONT — implement directly, or pull results
    back? — before starting, never after finishing.
  * Mid-run: a tiny out-of-scope item gets done and called out at the PR. A big missing
    design or scope is a decision point — stop and ask, weighed against whatever risk
    tolerance he stated in advance. A stated tolerance always wins over these defaults.

`add` clears a hold (new work declared means no longer waiting), as does `clear`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".claude", "hooks"))
from unfinished_work_lib import queue_path  # noqa: E402


# The Stop guard reads queue_path(<the harness's real session_id>). Anything else — most
# obviously a two-word CODENAME, which this repo's own convention actively encourages
# (feedback_session_codename_convention) — writes a declaration the guard will never read.
# That failed silently on 2026-07-29: three `--session wandering-horizon` declarations
# landed in wandering-horizon.json while the guard kept blocking on the real session id,
# and the file looked perfectly correct on disk. A declaration mechanism that silently does
# not declare is the same defect class as a test suite wired into no CI step.
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _resolve_session(raw, project_dir):
    """Map a session ref to the id the Stop guard reads. Returns (session_id, warning|None).

    Deliberately does NOT shell out to `sessions.py resolve`: that resolver only works once
    a session has LOGGED its note, which happens at session END, so mid-session — the only
    time this command is ever run — a codename essentially never resolves. Measured, it cost
    ~6.5s per invocation to reliably fail, on a command run at every turn boundary. The
    WARNING, not a resolver, is what actually prevents the silent failure.
    """
    if _SESSION_ID_RE.match(raw):
        return raw, None
    return raw, (
        f"WARNING: {raw!r} is not a harness session id, and could not be resolved to one\n"
        f"         (sessions.py only resolves a codename AFTER that session logs its note).\n"
        f"         Writing {raw}.json — but the Stop guard reads <session-id>.json, so this\n"
        f"         declaration is probably invisible to it.\n"
        f"         Get this session's real id from its lane file:\n"
        f"           ls -t .claude/scope/*.json | head -1   # basename = the session id")


def _session(args):
    # CLAUDE_CODE_SESSION_ID is what the harness actually exports; CLAUDE_SESSION_ID was a
    # guess and had never once matched, so auto-detection silently failed in EVERY session
    # (95 of 461 invocations died on "no session id" before anyone noticed). Both are read,
    # correct one first, so anything already exporting the legacy name keeps working.
    raw = (getattr(args, "session", None)
           or os.environ.get("CLAUDE_CODE_SESSION_ID")
           or os.environ.get("CLAUDE_SESSION_ID")
           or "")
    if not raw:
        print("no session id — pass --session or set CLAUDE_SESSION_ID", file=sys.stderr)
        raise SystemExit(3)
    sid, warning = _resolve_session(raw, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if warning:
        print(warning, file=sys.stderr)
    return sid


_TERMINAL_KEYS = ("hold", "claims", "discards", "limits")


def terminal_state(data):
    """Any recorded terminal declaration, or None.

    `hold` was the only terminal state in the obvious design. `claim`/`discard`/`limit` are this
    additions: a conclusion recorded, a conclusion deliberately dropped with a reason,
    or an explicitly bounded limit of what the facts could establish. All four end a
    turn legitimately; none of them is "nothing happened".
    """
    for k in _TERMINAL_KEYS:
        v = data.get(k)
        if v:
            return k, v
    return None


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {"items": []}
    except (OSError, IOError, ValueError):
        return {"items": []}


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", default=None)
    # --session ALSO on every subcommand, because `continuation.py hold "..." --session <id>`
    # is the order an agent reaches for once the bare form fails — and argparse rejected it
    # with "unrecognized arguments" 78 times. SUPPRESS (not None) is load-bearing: without it
    # an omitted subcommand-level flag would write None over a value given before the
    # subcommand, silently breaking the form that used to be the only working one.
    sess = argparse.ArgumentParser(add_help=False)
    sess.add_argument("--session", default=argparse.SUPPRESS,
                      help="harness session id (accepted before OR after the subcommand)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", parents=[sess]); a.add_argument("title")
    a.add_argument("--id", default=None)
    sub.add_parser("list", parents=[sess])
    d = sub.add_parser("done", parents=[sess]); d.add_argument("item_id")
    h = sub.add_parser("hold", parents=[sess],
                       help="delivered; blocked on a decision only you can make")
    h.add_argument("waiting_on")
    sub.add_parser("clear", parents=[sess])
    # --- THE RETARGET (work port, 2026-08-20) -------------------------------
    # Three more terminal states. For a builder the residue risk is a dropped TASK; for a
    # communicating analyst it is an UNRECORDED CONCLUSION. Same invariant — no session
    # ends with dangling state — pointed at the state that actually goes missing.
    c = sub.add_parser("claim", parents=[sess],
                       help="record a conclusion this session reached, and where it traces")
    c.add_argument("statement")
    c.add_argument("--source", default="", help="what it traces to (query, file, doc)")
    c.add_argument("--does-not-establish", default="",
                   help="the tempting conclusion this does NOT support")
    dc = sub.add_parser("discard", parents=[sess],
                        help="this session reached a conclusion not worth keeping, and why")
    dc.add_argument("why")
    lm = sub.add_parser("limit", parents=[sess],
                        help="a bounded 'I do not know' — what could not be established")
    lm.add_argument("what")
    args = ap.parse_args()

    path = queue_path(_session(args))
    data = _load(path)
    items = data.setdefault("items", [])

    if args.cmd == "add":
        item_id = args.id or re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")[:40]
        if any(i.get("id") == item_id and not i.get("done") for i in items):
            print(f"already queued: {item_id}"); return 0
        items.append({"id": item_id, "title": args.title, "done": False})
        # Declaring new work means the session is no longer waiting on you.
        data.pop("hold", None)
        _save(path, data); print(f"queued [{item_id}] {args.title}")
    elif args.cmd == "hold":
        # An open item and a hold are contradictory states ("I have work to do" vs "I am
        # waiting on you"). Holding closes the queue so the Stop guard sees ONE coherent
        # answer — otherwise the guard would keep blocking on the stale items and the
        # hold would never take effect, which is the bug this whole command exists to fix.
        for i in items:
            i["done"] = True
        data["hold"] = {"waiting_on": args.waiting_on}
        _save(path, data)
        print(f"holding — waiting on you: {args.waiting_on}")
    elif args.cmd == "list":
        open_items = [i for i in items if not i.get("done")]
        hold = data.get("hold") if isinstance(data.get("hold"), dict) else None
        if hold:
            print(f"  HOLD — waiting on you: {hold.get('waiting_on', '(unspecified)')}")
        for c in data.get("claims", []):
            src = c.get("source") or "UNSOURCED"
            print(f"  CLAIM — {c['statement']}  [{src}]")
            if c.get("does_not_establish"):
                print(f"          does not establish: {c['does_not_establish']}")
        for d in data.get("discards", []):
            print(f"  DISCARDED — {d['why']}")
        for l in data.get("limits", []):
            print(f"  LIMIT — could not establish: {l['what']}")
        declared = hold or data.get("claims") or data.get("discards") or data.get("limits")
        if not open_items:
            if not declared:
                print("continuation queue is empty")
        for i in open_items:
            print(f"  [{i['id']}] {i['title']}")
    elif args.cmd == "done":
        hit = False
        for i in items:
            if i.get("id") == args.item_id and not i.get("done"):
                i["done"] = True; hit = True
        _save(path, data)
        print(f"closed {args.item_id}" if hit else f"no open item {args.item_id!r}")
    elif args.cmd == "clear":
        for i in items:
            i["done"] = True
        data.pop("hold", None)
        _save(path, data); print("continuation queue cleared")
    # --- THE RETARGET -------------------------------------------------------
    # All three close the queue for the same reason `hold` does: an open item and a
    # recorded conclusion are contradictory answers to "where does this stand", and
    # the Stop guard must see exactly one.
    elif args.cmd == "claim":
        for i in items:
            i["done"] = True
        entry = {"statement": args.statement}
        if args.source:
            entry["source"] = args.source
        if args.does_not_establish:
            entry["does_not_establish"] = args.does_not_establish
        data.setdefault("claims", []).append(entry)
        data.pop("hold", None)
        _save(path, data)
        print(f"claim recorded — {args.statement}")
        if not args.source:
            # Not an error. A claim with no source is still better recorded than lost,
            # but an unsourced claim is exactly what cannot be defended when it is quoted
            # back, so the gap is named at the moment it is cheapest to close.
            print("  NOTE: no --source. This claim cannot be traced when someone "
                  "challenges it. Add one now if it exists.")
    elif args.cmd == "discard":
        for i in items:
            i["done"] = True
        data.setdefault("discards", []).append({"why": args.why})
        data.pop("hold", None)
        _save(path, data); print(f"discarded — {args.why}")
    elif args.cmd == "limit":
        for i in items:
            i["done"] = True
        data.setdefault("limits", []).append({"what": args.what})
        data.pop("hold", None)
        _save(path, data)
        print(f"limit recorded — could not establish: {args.what}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
