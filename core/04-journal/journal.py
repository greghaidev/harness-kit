#!/usr/bin/env python3
"""journal.py — capture that does not depend on remembering to capture.

THE GAP THIS CLOSES

The Stop guard makes a session declare where it stands, and the terminal verbs record a
CONCLUSION: what was established, what could not be, what was dropped. That is the durable
product of an analyst's session and it is now impossible to lose.

It is not the only thing worth keeping. A session also produces:

  * DECISIONS   — you chose something, and in three months nobody will remember why
  * FRICTION    — the tooling cost you twenty minutes, and it will cost you again
  * IDEAS       — worth doing, not now
  * QUESTIONS   — open threads someone must close

None of those are conclusions, so none of them trip the Stop guard, and capturing them was
left to the model reading the operating agreement and choosing to act. That is the
human-remembered rung of the ladder, and the whole argument for this kit is that the
human-remembered rung decays.

So: one command, and a daily roll-up that runs whether or not anyone remembers it.

    journal note "chose Postgres over the warehouse for the staging join" --kind decision
    journal note "the schema export tool drops enum defaults" --kind friction
    journal day                 # today's roll-up, built from the store
    journal roll --session <id> # append a session's own record; the Stop hook calls this
    journal recent 7

WHY A ROLL-UP AND NOT JUST NOTES

Individual notes are searchable and useless to read. A day is the smallest unit a person
actually revisits — "what did I do Tuesday" is a real question and "show me every note
tagged friction" is not. The roll-up is generated, never hand-written, and rebuilding it is
always safe because it is derived from the notes rather than being the record itself.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import sys

HARNESS = os.environ.get("HARNESS_HOME") or os.path.expanduser("~/harness")
sys.path.insert(0, os.path.join(HARNESS, "core", "01-memory"))

KINDS = ("decision", "friction", "idea", "question", "conclusion", "limit", "discard")

# Kinds a person writes by hand. The rest are produced by `roll` from terminal declarations.
HAND_KINDS = ("decision", "friction", "idea", "question")

TAG = "journal"
DAY_PREFIX = "journal-day-"

_store = None
_store_error = None
try:
    import agentos_store as _store  # type: ignore
except Exception as e:                                   # noqa: BLE001
    _store_error = e


def require_store():
    if _store is None:
        raise SystemExit(
            f"journal.py: memory store not importable ({_store_error}).\n"
            f"  Expected at {HARNESS}/core/01-memory — set HARNESS_HOME or re-run the installer.")
    return _store


def _tenant_default() -> str:
    return os.environ.get("HARNESS_TENANT", "work")


def _today(explicit: str | None = None) -> str:
    if explicit:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", explicit):
            raise SystemExit(f"journal.py: --date must be YYYY-MM-DD, got {explicit!r}")
        return explicit
    return _dt.datetime.now().strftime("%Y-%m-%d")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: str, n: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n] or "note"


# ---------------------------------------------------------------- write
def cmd_note(args) -> int:
    s = require_store()
    day = _today(args.date)
    note_id = f"{TAG}-{args.kind}-{day}-{_slug(args.text)}"
    body_lines = [args.text.strip()]
    if args.why:
        body_lines += ["", f"**Why:** {args.why.strip()}"]
    if args.session:
        body_lines += ["", f"session: {args.session}"]
    body_lines += ["", f"captured: {_now()}"]

    rec = s.put({
        "id": note_id,
        "title": f"{args.kind.upper()} — {args.text.strip()[:100]}",
        "type": "episodic",
        "tenant": args.tenant,
        "sensitivity": "internal",
        "egress": "cloud-ok",
        "status": "committed",
        "tags": [TAG, args.kind, f"day:{day}"],
        "body": "\n".join(body_lines),
    })
    print(f"{args.kind} recorded — {rec.get('id')}")
    if args.kind in ("decision", "question") and not args.why:
        # Not an error. A decision with no reasoning is still better kept than lost, but the
        # reason is the part that is worth anything in three months, and now is the cheapest
        # moment it will ever be to write it down.
        print("  NOTE: no --why. The reasoning is the part you will want later; add it now "
              "if you have it.")
    return 0


# ---------------------------------------------------------------- roll
def _session_declarations(project_dir: str | None = None) -> list[dict]:
    """Terminal declarations recorded by continuation.py today, as journal entries.

    The Stop guard already forces a session to declare where it stands. This turns that
    declaration into a journal entry for free — capture as a SIDE EFFECT of a gate that
    already exists, rather than as a second thing to remember.
    """
    base = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    qdir = pathlib.Path(base) / ".claude" / "state" / "continuation"
    out: list[dict] = []
    if not qdir.is_dir():
        return out
    for f in sorted(qdir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:                                # noqa: BLE001
            continue
        sid = f.stem
        for c in data.get("claims", []):
            out.append({"kind": "conclusion", "session": sid, "text": c.get("statement", ""),
                        "source": c.get("source", ""),
                        "not_established": c.get("does_not_establish", "")})
        for l in data.get("limits", []):
            out.append({"kind": "limit", "session": sid, "text": l.get("what", "")})
        for d in data.get("discards", []):
            out.append({"kind": "discard", "session": sid, "text": d.get("why", "")})
        hold = data.get("hold")
        if isinstance(hold, dict) and hold.get("waiting_on"):
            out.append({"kind": "question", "session": sid, "text": hold["waiting_on"]})
    return out


def cmd_roll(args) -> int:
    """Rebuild today's roll-up from the store plus this project's declarations.

    Idempotent by construction: the day note has a deterministic id and is regenerated,
    never appended to. Running it twice is the same as running it once, which is what
    makes it safe to call from a hook on every stop.
    """
    s = require_store()
    day = _today(args.date)
    hand = [n for n in s.search(query="", tenant=args.tenant, limit=500)
            if f"day:{day}" in (n.get("tags") or [])]
    declared = _session_declarations(args.project_dir)

    lines = [f"# {day}", ""]
    any_content = False

    def section(title: str, items: list[str]):
        nonlocal any_content
        if not items:
            return
        any_content = True
        lines.append(f"## {title}")
        lines.extend(f"- {i}" for i in items)
        lines.append("")

    for kind in HAND_KINDS:
        picked = [n.get("title", "").split("—", 1)[-1].strip()
                  for n in hand if kind in (n.get("tags") or [])]
        section(kind.capitalize() + "s", picked)

    concl = [f"{d['text']}"
             + (f"  \n  _source:_ {d['source']}" if d.get("source") else "  \n  _UNSOURCED_")
             + (f"  \n  _does not establish:_ {d['not_established']}"
                if d.get("not_established") else "")
             for d in declared if d["kind"] == "conclusion"]
    section("Conclusions recorded", concl)
    section("Limits", [d["text"] for d in declared if d["kind"] == "limit"])
    section("Discarded", [d["text"] for d in declared if d["kind"] == "discard"])
    section("Open questions", [d["text"] for d in declared if d["kind"] == "question"])

    if not any_content:
        if args.quiet:
            return 0
        print(f"journal: nothing recorded for {day}")
        return 0

    rec = s.put({
        "id": f"{DAY_PREFIX}{day}",
        "title": f"JOURNAL — {day}",
        "type": "episodic",
        "tenant": args.tenant,
        "sensitivity": "internal",
        "egress": "cloud-ok",
        "status": "committed",
        "tags": [TAG, "day-rollup", f"day:{day}"],
        "body": "\n".join(lines).strip(),
    })
    if not args.quiet:
        print(f"rolled {day} -> {rec.get('id')}")
        print("\n".join(lines).strip())
    return 0


# ---------------------------------------------------------------- read
def cmd_day(args) -> int:
    s = require_store()
    day = _today(args.date)
    got = None
    try:
        got = s.get(f"{DAY_PREFIX}{day}", args.tenant)
    except Exception:                                    # noqa: BLE001
        got = None
    if not got:
        print(f"journal: no roll-up for {day} yet — run `journal roll`")
        return 1
    print(got.get("body", ""))
    return 0


def cmd_recent(args) -> int:
    s = require_store()
    days = [n for n in s.search(query="", tenant=args.tenant, limit=500)
            if "day-rollup" in (n.get("tags") or [])]
    days.sort(key=lambda n: n.get("id", ""), reverse=True)
    if not days:
        print("journal: no roll-ups yet")
        return 0
    for n in days[: args.n]:
        body = n.get("body", "")
        headings = [l[3:] for l in body.split("\n") if l.startswith("## ")]
        counts = body.count("\n- ")
        print(f"  {n.get('id', '').replace(DAY_PREFIX, ''):<12s} {counts:>3d} entries   "
              + ", ".join(headings))
    return 0


# ---------------------------------------------------------------- cli
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="journal.py",
        description="Capture decisions, friction and ideas — and roll the day up automatically.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--tenant", default=_tenant_default())
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("note", help="record a decision, friction, idea or question")
    n.add_argument("text")
    n.add_argument("--kind", required=True, choices=HAND_KINDS)
    n.add_argument("--why", default="", help="the reasoning — the part worth anything later")
    n.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    n.add_argument("--date", default=None)

    r = sub.add_parser("roll", help="rebuild today's roll-up (idempotent; safe from a hook)")
    r.add_argument("--date", default=None)
    r.add_argument("--project-dir", default=None)
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--session", default="")

    d = sub.add_parser("day", help="print a day's roll-up")
    d.add_argument("--date", default=None)

    rc = sub.add_parser("recent", help="list recent roll-ups")
    rc.add_argument("n", nargs="?", type=int, default=14)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return {"note": cmd_note, "roll": cmd_roll, "day": cmd_day,
            "recent": cmd_recent}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
