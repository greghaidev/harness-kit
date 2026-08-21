#!/usr/bin/env python3
"""sessions.py — fast, model-free Agent OS session-log index ops (PAB Charter #11, Item A).

Sibling to `lane.py`: same in-process `agentos_store` import (zero LLM round-trip), same
body-as-fields storage convention (`lane.py`'s `_parse_item_fields`/`_ITEM_FIELD_LINE`/
comma-joined list-field pattern, ported verbatim below). Full schema + rationale:
docs/agent-os/session-log-schema.md.

  python3 scripts/sessions.py recent [N=15]                 # fixed-width table, most-recent first
  python3 scripts/sessions.py find <keyword>                 # case-insensitive grep over
                                                              # topic/artifacts/threads, last ~100
                                                              # session-log notes PLUS a
                                                              # FALLBACK_TAGS sweep (near-miss
                                                              # tags like "session-codename" from
                                                              # a hand-rolled note that skipped
                                                              # `log`) so a write-side slip is
                                                              # still surfaced, flagged `~`.
                                                              # Deterministic string matching —
                                                              # NEVER a wrapper over memory_search
                                                              # or any embedding/LLM call.
  python3 scripts/sessions.py show <id>                       # full note dump
  python3 scripts/sessions.py resolve <codename>               # "Copper Overlook" or
                                                              # "copper-overlook" -> the session-log
                                                              # note whose session_id hashes to that
                                                              # codename. Independent of what
                                                              # --session-ref that session actually
                                                              # used — this is the fast "operator
                                                              # names a codename, get the session
                                                              # back" path (see resolve's docstring).
  python3 scripts/sessions.py check --session <id> --since <ts>   # exit 0/1 (Item D backstop)
  python3 scripts/sessions.py log --session-id <id> --tenant work \\
      --topic "<one line>" --status closed|in-progress [--session-ref <ref>] \\
      [--resume-here "..."] \\
      [--artifacts "a,b"] [--follow-ups "a,b"] [--threads "a,b"] [--started ISO] [--ended ISO]
      # writes the note AND touches .claude/state/session-log-<session-id>.done — the marker
      # .claude/hooks/session-log-stop-nudge.sh checks for (never a store query from the hook).
      # --session-ref defaults to this session's codename slug (derived from --session-id) when
      # omitted, so `resolve`/`show <slug>` both work without the agent having to remember to
      # wire it manually.

`find`/`recent`/`show`/`resolve`/`check` are read-only and never write anything. `log` is the
only write path, and it is the ONLY place that touches the Item D marker file.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Same in-process store the repo's ops scripts use (or_agent.py). Machine-local checkout.
# The store lives wherever the kit was installed. HARNESS_HOME is set by the installer
# in ~/.claude/settings.json env; the default matches the installer's default target.
_HARNESS = os.environ.get("HARNESS_HOME") or os.path.expanduser("~/harness")
sys.path.insert(0, os.path.join(_HARNESS, "core", "01-memory"))
# DEFERRED, not fatal-at-import (2026-08-16). This used to sys.exit() the moment the store
# was missing, which meant `sessions.py --help` was unreadable on any box without a checkout
# of the harness install — including any box that never got one. That stopped being cosmetic when the
# recall recipe moved into the --help epilog (Decision D): help text has to be readable
# wherever you are. The error is unchanged and just as hard, it now fires when a command
# actually needs the store instead of when the module is imported.
store = None
_STORE_ERROR = None
try:
    import agentos_store as store  # noqa: F811
except Exception as e:  # pragma: no cover
    _STORE_ERROR = (f"sessions.py: Agent OS store not importable ({e}).\n"
                    f"  Set HARNESS_HOME, or re-run the kit installer.")


def require_store():
    """Every command that touches the store calls this first."""
    if store is None:
        sys.exit(_STORE_ERROR)

# Shared with the SessionStart/Stop codename hooks — a session's codename is a pure function of
# its session_id (session_codename_lib.codename_for). Importing it here (rather than
# re-implementing the hash) is what lets `resolve`/`log`'s --session-ref default work from the
# SAME codename a session was actually told at SessionStart, with no second source of truth.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                 ".claude", "hooks"))
try:
    from session_codename_lib import codename_for, slug_for
except Exception:  # pragma: no cover — codename resolution degrades gracefully, not fatally
    codename_for = slug_for = None

TAG = "session-log"
# Near-miss tags a session-end note gets written under when an agent hand-rolls a `memory_put`
# instead of running `sessions.py log` (the ONLY path that produces a real TAG-and-field-line
# note). Observed live 2026-07-25 (the TWOSTONE incident): a session wrote its close-out note
# with `tags: [..., "session-codename"]` — conceptually on-convention, mechanically invisible to
# `find`/`resolve`/`recent`, all of which filtered on TAG alone. Widening the read side to also
# sweep these tags is a retrieval-side defense-in-depth, NOT a substitute for the real fix (every
# session-end note going through `sessions.py log`) — a fallback note found this way usually has
# no `session_id:` field line, so `resolve_codename` still can't match it by codename; it only
# becomes newly visible to `find`/`recent`, clearly flagged as non-canonical (see `_fmt_row`).
FALLBACK_TAGS = ("session-codename",)
MAX_SCAN = 100          # "last ~100 session-log notes" per the charter
DEFAULT_RECENT = 15

# Body-line fields, in the schema's canonical order (docs/agent-os/session-log-schema.md).
FIELD_NAMES = ("session_id", "session_ref", "tenant", "started", "ended", "topic",
               "status", "resume_here", "artifacts", "follow_ups", "threads")
# Same field-line shape as lane.py's _ITEM_FIELD_LINE: "<field>: <rest of line>", first
# occurrence wins (a later duplicate line is ignored, never silently overwrites).
FIELD_LINE_RE = re.compile(r"^\s*(" + "|".join(FIELD_NAMES) + r")\s*:\s*(.*)$", re.I)

STATE_DIR_NAME = os.path.join(".claude", "state")


def _repo_root():
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1]


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_body(body):
    """Some notes get written with literal backslash-n as the line separator (mirrors
    lane.py's _normalize_body — same store, same footgun)."""
    return (body or "").replace("\\n", "\n")


def parse_fields(body):
    """Pull the session-log field lines out of a note body. First occurrence of a field
    wins (mirrors lane.py's _parse_item_fields)."""
    fields = {}
    for line in _normalize_body(body).splitlines():
        m = FIELD_LINE_RE.match(line)
        if m and m.group(1).lower() not in fields:
            fields[m.group(1).lower()] = m.group(2).strip()
    return fields


def split_list(s):
    """Comma-joined list field -> list of trimmed, non-empty strings."""
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def build_body(fields):
    """Render a session-log note body as `key: value` lines. List-shaped values
    (artifacts/follow_ups/threads) are comma-joined — same convention as lane.py's
    `surfaces` field."""
    lines = [
        "Session-log note (PAB Charter #11, Item A). Schema: "
        "docs/agent-os/session-log-schema.md.",
        "",
    ]
    for key in FIELD_NAMES:
        val = fields.get(key)
        if val in (None, "", [], ()):
            continue
        if isinstance(val, (list, tuple)):
            val = ", ".join(val)
        lines.append(f"{key}: {val}")
    return "\n".join(lines) + "\n"


def _sort_key(f):
    return f.get("ended") or f.get("started") or f.get("_updated") or ""


def _load_one(r, canonical):
    try:
        n = store.get(r["id"])
    except Exception:
        return None
    f = parse_fields(n.get("body", ""))
    f["_id"] = r.get("id")
    f["_title"] = n.get("title") or r.get("id")
    f["_updated"] = (n.get("frontmatter", {}) or {}).get("updated") or r.get("updated") or ""
    f["_canonical"] = canonical
    if not canonical and not f.get("topic"):
        # A fallback note almost never has body field-lines (it wasn't written by `log`) —
        # fall back to its title so it still reads usefully in `recent`/`find` output.
        f["topic"] = f["_title"]
    return f


def _load_session_notes(limit=MAX_SCAN, include_fallback=True):
    """[parsed-fields dict, ...] for the most recent `limit` session-log notes, plus (unless
    disabled) any notes caught by the FALLBACK_TAGS sweep that aren't already in that set.

    `store.search(tags=[...], ...)` is a pure TAG filter (no `query=`), the same in-process
    browse-mode lane.py already uses for `lane-item`/`lane-lock` — it is not the MCP
    `memory_search` tool and does not invoke BM25/embedding ranking. All keyword matching
    (`find`, below) happens afterward, in plain Python, over the fields this function returns.

    Each dict carries `_canonical` (True = written via `sessions.py log`, i.e. real TAG;
    False = swept in only via a FALLBACK_TAGS near-miss — see FALLBACK_TAGS docstring for why
    this exists and what it does NOT fix).
    """
    out = []
    seen = set()
    for r in store.search(tags=[TAG], limit=limit):
        seen.add(r["id"])
        f = _load_one(r, canonical=True)
        if f is not None:
            out.append(f)
    if include_fallback:
        for fallback_tag in FALLBACK_TAGS:
            for r in store.search(tags=[fallback_tag], limit=limit):
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                f = _load_one(r, canonical=False)
                if f is not None:
                    out.append(f)
    return out


def _fmt_row(f):
    ended = (f.get("ended") or "")[:19] or "(in-progress)"
    topic = (f.get("topic") or "")[:60]
    status = (f.get("status") or "")[:12]
    if not f.get("_canonical", True):
        status = ("~" + status)[:12]  # leading ~ flags a fallback-swept, non-canonical note
    n_art = len(split_list(f.get("artifacts")))
    n_fu = len(split_list(f.get("follow_ups")))
    return f"{ended:<20} {topic:<60} {status:<12} {n_art:>9} {n_fu:>11}   [{f.get('_id')}]"


def _print_table(notes):
    header = f"{'ended':<20} {'topic':<60} {'status':<12} {'artifacts':>9} {'follow_ups':>11}"
    print(header)
    print("-" * len(header))
    if not notes:
        print("(no session-log notes found)")
        return
    for f in notes:
        print(_fmt_row(f))
    if any(not f.get("_canonical", True) for f in notes):
        print("\n(~status = fallback-swept note, not written via `sessions.py log` — see "
              "FALLBACK_TAGS. `resolve <codename>` still won't match it without a session_id "
              "field; fix the write side, not this read.)")


# --------------------------------------------------------------------------- commands

def cmd_recent(args):
    notes = _load_session_notes(limit=max(args.n, MAX_SCAN))
    notes.sort(key=_sort_key, reverse=True)
    _print_table(notes[:args.n])
    return 0


def cmd_find(args):
    keyword = args.keyword.lower()
    notes = _load_session_notes(limit=MAX_SCAN)
    matches = []
    for f in notes:
        hay = " ".join([f.get("topic") or "", f.get("artifacts") or "",
                        f.get("threads") or ""]).lower()
        if keyword in hay:
            matches.append(f)
    matches.sort(key=_sort_key, reverse=True)
    _print_table(matches)
    return 0 if matches else 1


def _print_note(n):
    fm = n.get("frontmatter", {}) or {}
    print(f"id: {n.get('id')}")
    print(f"title: {n.get('title')}")
    for k in ("tenant", "tags", "created", "updated", "type"):
        if k in fm:
            print(f"{k}: {fm[k]}")
    print("---")
    print(n.get("body", ""), end="" if str(n.get("body", "")).endswith("\n") else "\n")


def cmd_show(args):
    try:
        n = store.get(args.id)
    except Exception as e:
        sys.exit(f"sessions.py show: not found ({args.id!r}): {e}")
    _print_note(n)
    return 0


def resolve_codename(codename_or_slug):
    """[parsed-fields dict, ...] of every session-log note whose session_id hashes to
    `codename_or_slug` (accepts either "Copper Overlook" or "copper-overlook").

    This is the reverse lookup `show`/`find` don't provide: a session's codename is a pure
    function of its session_id (session_codename_lib.codename_for), so resolution here does
    NOT depend on that session having remembered to set --session-ref to its codename slug —
    it recomputes the codename from session_id and compares directly. Scoped to the same
    "last ~100" window as find/recent (see MAX_SCAN)."""
    if codename_for is None:
        return []
    target = re.sub(r"[^a-z0-9]+", "-", codename_or_slug.strip().lower()).strip("-")
    matches = []
    for f in _load_session_notes(limit=MAX_SCAN):
        sid = f.get("session_id")
        if not sid:
            continue
        if slug_for(codename_for(sid)) == target:
            matches.append(f)
    matches.sort(key=_sort_key, reverse=True)
    return matches


def cmd_resolve(args):
    if codename_for is None:
        sys.exit("sessions.py resolve: session_codename_lib not importable "
                  "(.claude/hooks/session_codename_lib.py missing from this checkout)")
    matches = resolve_codename(args.codename)
    if not matches:
        print(f"NO MATCH: no session-log note found whose session_id hashes to "
              f"{args.codename!r}. Try `sessions.py find <keyword>` instead — that session may "
              f"not have logged a note yet, or the codename was mistyped.")
        return 1
    if len(matches) > 1:
        print(f"{len(matches)} session-log notes matched {args.codename!r} "
              f"(same codename, different sessions — finite wordlist, rare but possible):")
        _print_table(matches)
        return 0
    try:
        n = store.get(matches[0]["_id"])
    except Exception as e:
        sys.exit(f"sessions.py resolve: matched {matches[0]['_id']!r} but couldn't fetch it: {e}")
    _print_note(n)
    return 0


def cmd_check(args):
    """exit 0 if a session-log note for --session exists with ended/started >= --since,
    exit 1 otherwise. Read-only — this is a query helper, NOT what the Stop hook calls
    (the hook checks the marker file directly, never the store)."""
    for f in _load_session_notes(limit=MAX_SCAN):
        if f.get("session_id") != args.session:
            continue
        ts = f.get("ended") or f.get("started") or f.get("_updated") or ""
        if ts >= args.since:
            print(f"OK: {f.get('_id')} (session {args.session}, ts {ts} >= {args.since})")
            return 0
    print(f"NOT FOUND: no session-log for session {args.session!r} since {args.since!r}")
    return 1


def marker_path(session_id, repo_root=None):
    root = repo_root or _repo_root()
    return Path(root) / STATE_DIR_NAME / f"session-log-{session_id}.done"


def _touch_marker(session_id, repo_root=None):
    p = marker_path(session_id, repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_iso(_now()) + "\n")
    return p


def cmd_log(args):
    if args.status not in ("closed", "in-progress"):
        sys.exit("sessions.py log: --status must be closed|in-progress")
    if args.status == "in-progress" and not args.resume_here:
        sys.exit("sessions.py log: --resume-here is required when --status in-progress")
    topic = args.topic.strip()
    if not topic:
        sys.exit("sessions.py log: --topic must not be empty")
    if len(topic) > 120:
        sys.exit(f"sessions.py log: --topic must be <=120 chars (got {len(topic)})")

    now = _now()
    started = args.started or _iso(now)
    ended = args.ended or (_iso(now) if args.status == "closed" else "")

    session_ref = args.session_ref
    if not session_ref:
        if slug_for is None or codename_for is None:
            sys.exit("sessions.py log: --session-ref is required (session_codename_lib not "
                      "importable, so it can't be defaulted from the codename)")
        session_ref = slug_for(codename_for(args.session_id))

    fields = {
        "session_id": args.session_id, "session_ref": session_ref,
        "tenant": args.tenant, "started": started, "ended": ended, "topic": topic,
        "status": args.status,
    }
    if args.status == "in-progress":
        fields["resume_here"] = args.resume_here
    if args.artifacts:
        fields["artifacts"] = split_list(args.artifacts)
    if args.follow_ups:
        fields["follow_ups"] = split_list(args.follow_ups)
    if args.threads:
        fields["threads"] = split_list(args.threads)

    body = build_body(fields)
    slug = re.sub(r"[^a-z0-9]+", "-", session_ref.lower()).strip("-")[:56]
    note_id = f"session-log-{slug}"
    payload = {
        "id": note_id, "tenant": args.tenant, "type": "episodic",
        "title": f"SESSION LOG — {topic}"[:140],
        "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
        "tags": [TAG], "body": body,
    }
    store.put(payload)
    marker = _touch_marker(args.session_id)
    back = parse_fields(store.get(note_id).get("body", ""))
    print(f"LOGGED {note_id} · status {args.status} · marker {marker} · read-back "
          f"{'OK' if back.get('topic') == topic else 'FAILED'}")
    return 0


def digest_line(n=5):
    """One-liner for the memory-hygiene SessionStart digest (scripts/memory_hygiene.py
    cmd_status — the SAME injection point, not a second one). Never raises: an
    unreachable store degrades to a pointer line, exactly like the rest of that digest's
    fail-open posture."""
    try:
        notes = _load_session_notes(limit=max(n, MAX_SCAN))
    except Exception as e:
        return f"Recent sessions: unavailable ({e}). Run: python3 scripts/sessions.py recent"
    notes.sort(key=_sort_key, reverse=True)
    notes = notes[:n]
    if not notes:
        return "Recent sessions: none logged yet. Run: python3 scripts/sessions.py recent"
    latest = notes[0]
    topic = (latest.get("topic") or "")[:60]
    when = latest.get("ended") or latest.get("started") or "?"
    return (f"Recent sessions: {len(notes)} shown, latest \"{topic}\" ({when}). "
            f"Full table: python3 scripts/sessions.py recent 5")


# --------------------------------------------------------------------------------- CLI

def build_parser():
    ap = argparse.ArgumentParser(prog="sessions.py", description=__doc__,
                                 epilog=RECALL_RECIPE,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_recent = sub.add_parser("recent")
    p_recent.add_argument("n", nargs="?", type=int, default=DEFAULT_RECENT)
    p_recent.set_defaults(func=cmd_recent)

    p_find = sub.add_parser("find")
    p_find.add_argument("keyword")
    p_find.set_defaults(func=cmd_find)

    p_show = sub.add_parser("show")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("codename")
    p_resolve.set_defaults(func=cmd_resolve)

    p_check = sub.add_parser("check")
    p_check.add_argument("--session", required=True)
    p_check.add_argument("--since", required=True)
    p_check.set_defaults(func=cmd_check)

    p_log = sub.add_parser("log")
    p_log.add_argument("--session-id", required=True)
    p_log.add_argument("--session-ref",
                        help="defaults to this session's codename slug (derived from "
                             "--session-id) when omitted")
    p_log.add_argument("--tenant", required=True, choices=["work", "meta"])
    p_log.add_argument("--topic", required=True)
    p_log.add_argument("--status", required=True, choices=["closed", "in-progress"])
    p_log.add_argument("--resume-here")
    p_log.add_argument("--started")
    p_log.add_argument("--ended")
    p_log.add_argument("--artifacts")
    p_log.add_argument("--follow-ups")
    p_log.add_argument("--threads")
    p_log.set_defaults(func=cmd_log)

    return ap


# ---------------------------------------------------------------------------
# The recall recipe. Was the `session-recall` SKILL until 2026-08-16 (Decision D):
# 4 skill invocations against 260 calls to this tool, because a fetch-shaped job
# spends a whole turn loading instructions BEFORE the call that answers it. It
# lives in --help now, where it is read at the moment of use and costs nothing in
# every session that never needs it. Content is the skill's, unchanged in substance.
# ---------------------------------------------------------------------------
RECALL_RECIPE = """
RESOLVING AN OPERATOR'S RECALL POINTER
--------------------------------------
Two shapes, two paths. Never a single silent guess, never an empty-handed return.

1. A LITERAL CODENAME  ("review copper overlook", "resume Cobalt Meridian")
       python3 scripts/sessions.py resolve <codename-or-slug>
   Exact and deterministic: a codename is a pure function of the session_id, so
   `resolve` recomputes and matches it REGARDLESS of what --session-ref that
   session logged under (they often differ for older sessions). One match IS the
   answer — confirm topic + date + artifact and proceed, do not offer it as a
   guess. Zero matches means that session never logged a note; say so plainly,
   then fall through to (2) using the codename words as `find` tokens.

2. A FUZZY POINTER  ("last night, the pricing thing", "the one where the load job broke")
       python3 scripts/sessions.py find <keyword>      # PRIMARY — deterministic
   `find` is string matching over topic/artifacts/threads across the last ~100
   session-log notes, plus a fallback-tag sweep for hand-rolled notes that skipped
   `log` (flagged `~`). It is NEVER a wrapper over memory_search or embeddings.
   Use memory_search only as a SECONDARY supplement, then present 2-3 NAMED
   candidates for the operator to pick from.

A precise date, session ID, PR number or exact quote is direct lookup — just use
`find` or `show` and skip the disambiguation.
"""


def main(argv=None):
    args = build_parser().parse_args(argv)
    # --help/-h never reaches here (argparse exits first), which is the point: help text
    # is readable on any box. Every real subcommand touches the store, so gate here.
    require_store()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
