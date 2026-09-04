#!/usr/bin/env python3
"""lanes.py — the parallel-work board: what is running, what is queued, what is waiting on you.

THE GAP THIS CLOSES

The store makes conclusions durable and the journal makes the day durable. Neither answers the
question you are actually asked on a Monday morning, which is some version of *what is happening
to my request*. That question has no home. It lives in your head, in four Slack threads, and in
whatever a given session happened to mention — so the only reliable way to answer it is to
reconstruct it, every time, from scratch.

Worse, nothing stops two agents working the same files at once. A session that opens a second
Claude Code window is running two writers against one checkout with no coordination beyond your
memory of what the other one was doing.

So: work is organized into LANES. A lane is a parallel workstream, and exactly one worker holds
a lane at a time. Claiming a lane declares the SURFACES it will touch (files, dirs, systems), and
the claim is refused when those surfaces overlap a lane somebody else already holds. Items queue
into lanes with an approval gate between "an agent proposed this" and "I said do it". One command
renders everything genuinely waiting on you, each with the exact command that clears it.

  lanes board                     # the whole picture
  lanes claim L2 --task "..." --surfaces "etl/dim_customer.sql, docs/lineage"
  lanes add L2 --title ... --what ... --origin agent
  lanes needs-you                 # everything gated on you, nothing else
  lanes reconcile --apply         # close what provably shipped

THE THREE RULES THAT MAKE IT WORTH THE TROUBLE

**A lock is read back after it is written.** A silently-failed lock is worse than no lock: it
reports coordination that is not happening. Every write here re-reads and says so.

**An inference never masquerades as a verified fact.** `reconcile` closes an item only on a hard,
re-checkable reference the item itself carries — a merged PR number, or a branch provably merged
into the trunk. Slug similarity is a *hint*, surfaced for a human, never a closing signal. When
the PR host is unreachable the run says SKIPPED, loudly, rather than rendering an empty result
that is indistinguishable from a clean board.

**Approval is recorded, never inferred.** `approve` demands evidence with a source prefix, and an
unattended session cannot approve at all. The gate exists precisely so an agent cannot walk
through it.

STATE LIVES IN THE STORE, not in a file beside this script. Lanes, locks and items are notes, so
they are searchable, linkable to the follow-ups they close, and backed up by the same git commit
that backs up everything else.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HARNESS = Path(os.path.expanduser(os.environ.get("HARNESS_HOME") or "~/harness"))
sys.path.insert(0, str(HARNESS / "core" / "01-memory"))

try:
    import agentos_store as store
except Exception as e:  # pragma: no cover - install-time failure, proven by verify.py
    sys.exit(f"lanes.py: the store is not importable ({e}).\n"
             f"  Expected it at {HARNESS / 'core' / '01-memory' / 'agentos_store.py'}.\n"
             f"  Set HARNESS_HOME if the kit lives somewhere else.")


# ─────────────────────────────────────────────────────────────────────────────── configuration
#
# Everything site-specific is here and nowhere else, because the alternative is a fork. The
# defaults are a working board on their own; `lanes config` prints what is in force.

CONFIG_PATH = Path(os.path.expanduser(
    os.environ.get("HARNESS_LANES_CONFIG") or str(HARNESS / "lanes.json")))

DEFAULT_CONFIG = {
    # Which store partition lane state lives in. `work` is the default partition the kit installs.
    "tenant": "work",
    # Path to the repository whose PRs and branches are the ground truth for "did this ship".
    # null disables ground-truth checking — reported loudly at every point it would have run,
    # never silently treated as "nothing has shipped".
    "repo": None,
    # The branch a merge lands on. `git merge-base --is-ancestor <branch> <trunk>` is the
    # fallback shipped-detector when no PR host is reachable.
    "trunk": "origin/main",
    # Whether to ask a `gh`-compatible CLI for PR state. Set false on hosts without one.
    "pr_cli": "gh",
    # The lane series, in the order the board renders them. `contended` lanes are the ones
    # `pick` rotates through by default; the rest are background work that does not compete.
    "series": [
        {"key": "L", "label": "Line work — the main queue", "contended": True, "ttl_days": 14},
        {"key": "O", "label": "Ongoing — background, not in contention", "contended": False,
         "ttl_days": 21},
        {"key": "R", "label": "Research / tooling", "contended": False, "ttl_days": 21},
    ],
    # Who may file work, and what each origin means for the approval gate.
    "origins": ["me", "agent", "monitor", "stakeholder"],
    # An `agent`-origin item at this tier enters `approved` without a click. Set to [] to make
    # every agent-filed item wait for you. See cmd_add for the argument on both sides.
    "agent_auto_approve_tiers": ["green"],
    # Advisory ceilings. Over budget, the board says so at the top of every operator surface.
    "budget": {"awaiting_operator": 10, "open_followups": 150},
}


def _load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
        except Exception as e:
            sys.exit(f"lanes.py: {CONFIG_PATH} is not readable JSON ({e}).")
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


CFG = _load_config()
TENANT = CFG["tenant"]
SERIES = [s["key"].upper() for s in CFG["series"]]
SERIES_META = {s["key"].upper(): s for s in CFG["series"]}
SERIES_ORDER = {k: i for i, k in enumerate(SERIES)}
_TTL_DAYS = {k: int(SERIES_META[k].get("ttl_days", 14)) for k in SERIES}
_ORIGINS = tuple(CFG["origins"])
_REPO = Path(os.path.expanduser(CFG["repo"])) if CFG.get("repo") else None
_TRUNK = CFG.get("trunk") or "origin/main"
_PR_CLI = CFG.get("pr_cli") or None
_AUTO_TIERS = {t.lower() for t in CFG.get("agent_auto_approve_tiers") or []}
_BUDGET_AWAITING = int(CFG["budget"]["awaiting_operator"])
_BUDGET_FOLLOWUPS = int(CFG["budget"]["open_followups"])

_LANE_RE = re.compile(r"^([" + "".join(SERIES) + r"])(\d+)$", re.I)
LANE_DEF_RE = re.compile(r"^lane-def-([" + "".join(SERIES).lower() + r"]\d+)$")
LOCK_RE = re.compile(r"^lane-lock-([" + "".join(SERIES).lower() + r"]\d+)$")
ITEM_RE = re.compile(r"^lane-item-([" + "".join(SERIES).lower() + r"]\d+)-(.+)$")

REGISTER_ID = "lane-priority-register"

_STATUSES = ("proposed", "approved", "in-progress", "done", "dropped", "open", "resolved")
_KINDS = ("work", "incident")
_SEVERITIES = ("info", "warn", "crit")
_TIERS = ("green", "yellow", "red")
_DISPOSITIONS = ("open", "parked", "blocked", "closed")
_PROPOSED_ROT_DAYS = 14
_REGISTER_STALE_DAYS = 10
_EVIDENCE_RE = re.compile(r"^(operator-add|session|cmd|pr|quote|note):\s*\S", re.I)

# The ranked classes a priority register may use, highest first. A class is by NATURE, not by
# how much you feel like doing it, which is the only reason a ranked list survives contact with
# a busy week.
_RANK_CLASSES = ("security", "compliance", "stakeholder-impact", "usability",
                 "nearly-done", "early-wip", "new")
_REG_ENTRY_RE = re.compile(r"^\s*(\d+)\.\s*\[([a-z][a-z-]*)\]\s*ref:(\S+)\s*[—–-]\s*(.+)$")

DISPO_ICON = {"open": "▫️", "parked": "⏸", "blocked": "⛔", "closed": "✅"}
DISPO_PHRASE = {"open": "**Placed / free**", "parked": "**Parked / frozen**",
                "blocked": "**Blocked on you**", "closed": "**Closed / done**",
                "held": "**ACTIVE / held**"}


# ─────────────────────────────────────────────────────────────────────────────────── primitives

def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s):
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _normalize_body(body):
    """Some bodies arrive with literal backslash-n as the separator (a JSON round-trip through a
    tool that did not decode it), sometimes mixed with real newlines. Convert unconditionally so
    field parsing never depends on which path wrote the note."""
    return body.replace("\\n", "\n") if body else body


def _lane_key(raw):
    m = _LANE_RE.match((raw or "").strip())
    if not m:
        sys.exit(f"bad lane '{raw}' — expect a series letter + number, e.g. "
                 f"{SERIES[0]}1 (series in force: {', '.join(SERIES)}).")
    return f"{m.group(1).upper()}{m.group(2)}"


def _first_sentence(text, limit=150):
    text = re.sub(r"\s+", " ", text or "").strip()
    cut = re.split(r"(?<=[.)]) ", text, maxsplit=1)[0]
    return (cut[:limit] + "…") if len(cut) > limit else cut


def _cell(text, limit=95):
    """Table-cell-safe: drop [[wikilink]] ids, escape pipes, collapse whitespace, cap."""
    t = re.sub(r"\[\[[^\]]+\]\]", "", text or "")
    t = re.sub(r"\s+", " ", t).strip(" .,;→").replace("|", r"\|")
    return (t[:limit].rstrip() + "…") if len(t) > limit else t


def _fields_re(names):
    return re.compile(r"^\s*(" + "|".join(names) + r")\s*:\s*(.*)$", re.I)


def _parse_fields(body, pattern):
    """First occurrence of each field wins, so prose further down a note that happens to start
    'status:' cannot overwrite the real field."""
    out = {}
    for line in _normalize_body(body or "").splitlines():
        m = pattern.match(line)
        if m and m.group(1).lower() not in out:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def _set_fields(body, updates, order, pattern):
    """Update fields IN PLACE, preserving the header line and any trailing prose. Replaces the
    first line of each known field; appends any field with no line yet, right after the block."""
    lines = _normalize_body(body or "").splitlines()
    seen, out, last_idx = set(), [], -1
    for line in lines:
        m = pattern.match(line)
        key = m.group(1).lower() if m else None
        if key in updates and key not in seen:
            out.append(f"{key}: {updates[key]}")
            seen.add(key)
            last_idx = len(out) - 1
        else:
            if m:
                last_idx = len(out)
            out.append(line)
    missing = [(k, updates[k]) for k in order if k in updates and k not in seen]
    ins = (last_idx + 1) if last_idx >= 0 else len(out)
    for j, (k, v) in enumerate(missing):
        out.insert(ins + j, f"{k}: {v}")
    return "\n".join(out).rstrip("\n") + "\n"


def _put(note_id, title, body, tags, existing=None):
    """Write a note, preserving the frontmatter of an existing one.

    The store validates before it carries anything over, so an update must re-supply the required
    frontmatter rather than assume it. Title and note-lifecycle status are deliberately left
    alone: lane state lives in the BODY, and moving it into the frontmatter would make a released
    lock look like an archived note.
    """
    if existing:
        fm = existing["frontmatter"]
        payload = {
            "id": existing["id"], "tenant": existing["tenant"],
            "type": fm.get("type", "procedural"),
            "title": existing.get("title") or fm.get("title") or title,
            "sensitivity": fm.get("sensitivity", "internal"),
            "egress": fm.get("egress", "cloud-ok"),
            "status": fm.get("status", "committed"),
            "tags": fm.get("tags", tags), "body": body,
        }
    else:
        payload = {
            "id": note_id, "tenant": TENANT, "type": "procedural", "title": title,
            "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
            "tags": tags, "body": body,
        }
    return store.put(payload)


def _try_get(note_id):
    try:
        return store.get(note_id)
    except Exception:
        return None


def _holder_id():
    """Who is holding this lane. The session id when a harness supplies one, because that is what
    a later reader can actually resolve back to a transcript."""
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if sid:
        return f"session-{sid[:8]}"
    named = (os.environ.get("HARNESS_HOLDER") or "").strip()
    if named:
        return named
    import socket
    return f"{socket.gethostname()}-{os.getpid()}"


def _unattended():
    """True when this session is running without a person watching it.

    Load-bearing for exactly one thing: an unattended session may not approve its own work. The
    signal is explicit rather than inferred, because every inferred version of this check has the
    same failure — a concurrent session's marker makes an interactive session look unattended and
    blocks a real yes.
    """
    return (os.environ.get("HARNESS_UNATTENDED") or "").strip().lower() in ("1", "true", "yes")


# ─────────────────────────────────────────────────────────────────────────────── lane definitions
#
# A lane is a note, not a row in a hand-maintained index. That is a deliberate change: an index
# note has to be edited by hand to add a lane, so a lane with work queued into it but no index
# row is INVISIBLE on the board — the board silently under-reports exactly when it matters.

LANE_FIELDS = ("lane", "title", "rank", "disposition", "gate", "blocked_on", "accel")
_LANE_FIELD_RE = _fields_re(LANE_FIELDS)


def _load_lanes():
    """key -> fields for every lane-def note."""
    out = {}
    for r in store.search(tenant=TENANT, tags=["lane-def"], limit=300):
        m = LANE_DEF_RE.match(r.get("id", ""))
        if not m:
            continue
        n = _try_get(r["id"])
        if not n:
            continue
        f = _parse_fields(n.get("body", ""), _LANE_FIELD_RE)
        f["_id"], f["_note"] = r["id"], n
        f["_key"] = (f.get("lane") or m.group(1)).upper()
        out[f["_key"]] = f
    return out


def _lane_sort_key(key, lanes):
    f = lanes.get(key, {})
    try:
        rank = int(f.get("rank") or 999)
    except ValueError:
        rank = 999
    num = int(re.sub(r"\D", "", key) or 0)
    return (SERIES_ORDER.get(key[0], 99), rank, num)


def cmd_lane_add(args):
    key = _lane_key(args.lane)
    lane_id = f"lane-def-{key.lower()}"
    existing = _try_get(lane_id)
    if existing and not args.force:
        sys.exit(f"{lane_id} already exists — use `lanes lane-set {key} ...` to change it, "
                 f"or --force to overwrite.")
    if args.disposition not in _DISPOSITIONS:
        sys.exit(f"--disposition must be one of {_DISPOSITIONS}")
    fields = {"lane": key, "title": args.title, "rank": str(args.rank),
              "disposition": args.disposition, "gate": args.gate or "",
              "blocked_on": args.blocked_on or "", "accel": args.accel or ""}
    body = _set_fields(f"Lane definition. One workstream; one holder at a time.\n\n",
                       fields, LANE_FIELDS, _LANE_FIELD_RE)
    _put(lane_id, f"LANE {key} — {args.title}", body, ["lane-def", "lane-board"],
         existing if args.force else None)
    back = _parse_fields(store.get(lane_id)["body"], _LANE_FIELD_RE)
    print(f"LANE {key} defined · rank {back.get('rank')} · {back.get('disposition')} "
          f"· read-back OK")


def cmd_lane_set(args):
    key = _lane_key(args.lane)
    lane_id = f"lane-def-{key.lower()}"
    n = _try_get(lane_id)
    if not n:
        sys.exit(f"no lane {key} — define it first:\n"
                 f"  lanes lane-add {key} --title \"...\" --rank <n>")
    updates = {}
    for attr in ("title", "gate", "accel"):
        if getattr(args, attr) is not None:
            updates[attr] = getattr(args, attr)
    if args.rank is not None:
        updates["rank"] = str(args.rank)
    if args.blocked_on is not None:
        updates["blocked_on"] = args.blocked_on
        # A lane with a live block IS blocked. Leaving disposition untouched here is how a board
        # ends up rendering "free" over a lane nothing can move on.
        if args.blocked_on and args.disposition is None:
            updates["disposition"] = "blocked"
        if not args.blocked_on and args.disposition is None:
            updates["disposition"] = "open"
    if args.disposition is not None:
        if args.disposition not in _DISPOSITIONS:
            sys.exit(f"--disposition must be one of {_DISPOSITIONS}")
        updates["disposition"] = args.disposition
    if not updates:
        sys.exit("nothing to set")
    body = _set_fields(n["body"], updates, LANE_FIELDS, _LANE_FIELD_RE)
    _put(lane_id, n.get("title", ""), body, ["lane-def", "lane-board"], n)
    print(f"SET {key} · " + " · ".join(f"{k}={v or '(cleared)'}" for k, v in updates.items()))


# ──────────────────────────────────────────────────────────────────────────────────────── locks

LOCK_FIELDS = ("status", "holder", "task", "surfaces", "claimed_at", "lease_until")
_LOCK_FIELD_RE = _fields_re(LOCK_FIELDS)


def _load_locks():
    out = {}
    for r in store.search(tenant=TENANT, tags=["lane-lock"], limit=300):
        m = LOCK_RE.match(r.get("id", ""))
        if not m:
            continue
        n = _try_get(r["id"])
        if not n:
            continue
        f = _parse_fields(n.get("body", ""), _LOCK_FIELD_RE)
        f["_id"], f["_key"] = r["id"], m.group(1).upper()
        out[f["_key"]] = f
    return out


def _held(lock, now):
    """(actively_held, lease_expired). A lock whose lease has run out is treated as FREE — the
    agent that took it is gone, and a board that shows a dead agent's lock forever is a board
    people learn to ignore."""
    status = (lock.get("status") or "").split()[0].lower() if lock.get("status") else ""
    if status != "active":
        return False, False
    lease = _parse_ts(lock.get("lease_until"))
    if lease is None:
        return True, False
    return (lease > now, lease <= now)


# Bare one-segment names almost every lane touches. An overlap on these ALONE is not a conflict;
# refusing on them would make the collision check fire constantly and get disabled within a week.
_STOP_SURFACE = {"docs", "data", "scripts", "src", "tests", "test", "sql", "notebooks",
                 "prod", "staging", "dev", "db", "read", "edit", "write", "new", "the", "and"}


def _surface_tokens(text):
    """Full paths, plus two-segment prefixes (the same-subdirectory signal), plus distinctive
    bare names. Two lanes in the same subdirectory are a conflict worth stopping for; two lanes
    that both mention `docs/` are not."""
    toks = set()
    for raw in re.split(r"[,\s;]+", (text or "").lower()):
        raw = raw.strip("().[]<>*—·,")
        if len(raw) < 4:
            continue
        if "/" in raw:
            toks.add(raw)
            segs = [s for s in raw.split("/") if s]
            if len(segs) >= 2:
                toks.add("/".join(segs[:2]))
        elif "." in raw or raw.isalpha():
            toks.add(raw)
    return toks


def _surface_overlap(a, b):
    return (a & b) - _STOP_SURFACE


def _active_surfaces(locks, now, exclude):
    out = {}
    for key, lock in locks.items():
        if key == exclude:
            continue
        if _held(lock, now)[0] and lock.get("surfaces"):
            out[key] = lock["surfaces"]
    return out


def cmd_claim(args):
    key = _lane_key(args.lane)
    lock_id = f"lane-lock-{key.lower()}"
    now = _now()
    lanes = _load_lanes()
    if key not in lanes and not args.force:
        sys.exit(f"no lane {key} is defined, so nothing would render it on the board.\n"
                 f"  Define it:  lanes lane-add {key} --title \"<what this lane is>\" --rank <n>\n"
                 f"  Or --force to lock it anyway (it will show as an undefined lane).")

    n = _try_get(lock_id)
    if n:
        f = _parse_fields(n.get("body", ""), _LOCK_FIELD_RE)
        held, stale = _held(f, now)
        if held and not args.force:
            sys.exit(f"REFUSED: {key} is HELD by {f.get('holder', '?')} "
                     f"(lease_until {f.get('lease_until', '?')}).\n"
                     f"  Wait for the lease, or record why you are preempting in the lock note "
                     f"and re-run with --force.")
        if stale:
            print(f"note: {key}'s lock was active but its lease expired — treating it as free.")

    locks = _load_locks()
    mine = _surface_tokens(args.surfaces)
    collisions = []
    for other, surf in _active_surfaces(locks, now, exclude=key).items():
        overlap = _surface_overlap(mine, _surface_tokens(surf))
        if overlap:
            collisions.append((other, sorted(overlap)))
    if collisions and not args.force:
        msg = "; ".join(f"{o} on {', '.join(ov)}" for o, ov in collisions)
        sys.exit(f"REFUSED: surface collision with lane(s) already held: {msg}.\n"
                 f"  Queue behind the holder, narrow your --surfaces, or record why the work is "
                 f"genuinely disjoint and re-run with --force.")

    holder = args.holder or _holder_id()
    lease = now + timedelta(hours=args.hours)
    seed = n.get("body") if n else f"Lock for lane {key}.\n\n"
    body = _set_fields(seed, {"status": "active", "holder": holder, "task": args.task,
                              "surfaces": args.surfaces, "claimed_at": _iso(now),
                              "lease_until": _iso(lease)}, LOCK_FIELDS, _LOCK_FIELD_RE)
    if args.dry_run:
        print(f"[dry-run] would write {lock_id} "
              f"(collisions: {collisions or 'none'}):\n---\n{body}---")
        return

    _put(lock_id, f"LANE LOCK — {key}", body, ["lane-lock", "lane-board"], n)

    # Mandatory read-back. A lock that failed to write looks exactly like one that succeeded from
    # the caller's side, and reporting coordination that is not happening is worse than none.
    back = _parse_fields(store.get(lock_id).get("body", ""), _LOCK_FIELD_RE)
    if back.get("status", "").startswith("active") and back.get("holder") == holder:
        print(f"LOCKED {key} · holder {holder} · lease {_iso(lease)} · read-back OK")
    else:
        sys.exit(f"WARNING: read-back did not confirm the claim — inspect {lock_id} by hand.")


def cmd_heartbeat(args):
    key = _lane_key(args.lane)
    lock_id = f"lane-lock-{key.lower()}"
    n = _try_get(lock_id)
    if not n:
        sys.exit(f"no lock on {key} — claim it first.")
    if not _parse_fields(n["body"], _LOCK_FIELD_RE).get("status", "").startswith("active"):
        sys.exit(f"{key} is not active — claim it first.")
    lease = _now() + timedelta(hours=args.hours)
    body = _set_fields(n["body"], {"lease_until": _iso(lease)}, LOCK_FIELDS, _LOCK_FIELD_RE)
    _put(lock_id, n.get("title", ""), body, ["lane-lock", "lane-board"], n)
    print(f"heartbeat {key} · lease → {_iso(lease)}")


def cmd_release(args):
    key = _lane_key(args.lane)
    lock_id = f"lane-lock-{key.lower()}"
    n = _try_get(lock_id)
    if not n:
        sys.exit(f"no lock on {key} — nothing to release.")
    body = _set_fields(n["body"], {"status": "released"}, LOCK_FIELDS, _LOCK_FIELD_RE)
    _put(lock_id, n.get("title", ""), body, ["lane-lock", "lane-board"], n)
    print(f"released {key}")


# ──────────────────────────────────────────────────────────────────────────────────────── items
#
# An item is one piece of work in one lane, with a lifecycle: proposed → approved → in-progress →
# done (dropped from anywhere). `blocked_on` is an OVERLAY, not a state, so a blocked item stays
# visible on the board instead of disappearing into a status nobody looks at.
#
# Incidents are a second kind, and keeping them distinct is the whole point. An incident is a
# thing that is BROKEN; a work item is a thing awaiting a decision. Filing them into one queue
# means the queue you check for "what needs me" fills with machine noise, and you stop checking
# it. An incident never enters the approval queue and never counts against the awaiting-you
# budget; it closes on positive evidence of health, never on a yes.

ITEM_FIELDS = ("lane", "status", "origin", "tier", "size", "what", "done_means", "surfaces",
               "approved_by", "approved_at", "approved_until", "evidence", "scope_hash",
               "artifact", "blocked_on", "accel", "holder", "closed_by", "kind", "severity",
               "raw_evidence", "first_seen", "resolved_at", "triage")
_ITEM_FIELD_RE = _fields_re(ITEM_FIELDS)


def _scope_hash(what, done_means):
    """A fingerprint of what was approved. If the scope is edited afterwards, the approval no
    longer covers it, and the board says so instead of letting rewritten work inherit an old yes."""
    norm = re.sub(r"\s+", " ", f"{what}|{done_means}".lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:10]


def _load_items():
    out = {}
    for r in store.search(tenant=TENANT, tags=["lane-item"], limit=1000):
        m = ITEM_RE.match(r.get("id", ""))
        if not m:
            continue
        n = _try_get(r["id"])
        if not n:
            continue
        f = _parse_fields(n.get("body", ""), _ITEM_FIELD_RE)
        f["_id"], f["_note"] = r["id"], n
        f["_created"] = n.get("frontmatter", {}).get("created", "")
        f["_lane"] = (f.get("lane") or m.group(1)).upper()
        f["_slug"] = m.group(2)
        out[r["id"]] = f
    return out


def _find_item(items, ref):
    if ref in items:
        return items[ref]
    hits = [f for i, f in items.items() if ref.lower() in i.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"no item matching '{ref}' — see `lanes queue`.")
    sys.exit(f"item ref '{ref}' matched {len(hits)} items — use the full id:\n  "
             + "\n  ".join(sorted(h["_id"] for h in hits)))


def _item_flags(f, now):
    """(eligible, badges). Computed live at every read, never cached — a stored eligibility flag
    is a second copy of the truth, free to drift from the one that matters."""
    badges = []
    status = (f.get("status") or "").lower()
    if status == "approved":
        until = _parse_ts(f.get("approved_until"))
        if until and until < now:
            badges.append(f"STALE approval (expired {(now - until).days}d ago — re-verify)")
        if f.get("scope_hash") and _scope_hash(f.get("what", ""),
                                               f.get("done_means", "")) != f["scope_hash"]:
            badges.append("SCOPE CHANGED since approval — re-approval required")
    if status == "proposed":
        created = _parse_ts(f.get("_created"))
        if created and (now - created).days > _PROPOSED_ROT_DAYS:
            badges.append(f"ROTTING proposed ({(now - created).days}d awaiting review)")
    if f.get("blocked_on"):
        badges.append(f"⛔ {f['blocked_on']}"
                      + (f" · how: {f['accel']}" if f.get("accel") else ""))
    return status == "approved" and not badges, badges


def _item_put(f, body):
    n = f["_note"]
    return _put(f["_id"], n.get("title", ""), body, ["lane-item", "lane-board"], n)


# ───────────────────────────────────────────────────────────────────────────────── ground truth
#
# The queue is a CACHE. Version control is the authority on whether something shipped. Every
# function here is allowed to say "I could not check"; none is allowed to render that as "no".

_PR_INDEX_CACHE = {}
_PR_REF_RE = re.compile(r"(?:pull/|PR\s*#|(?<![\w/])#)(\d{1,6})\b", re.I)
_BRANCH_REF_RE = re.compile(r"\bbranch\s+([A-Za-z0-9._/-]{3,80})")


def _repo_ready():
    return _REPO is not None and (_REPO / ".git").exists()


def _why_no_pr():
    """Why a PR check could not run — named precisely, because 'unavailable' covers three
    different situations and the fix differs for each."""
    if _REPO is None:
        return "no repo configured in lanes.json"
    if not (_REPO / ".git").exists():
        return f"{_REPO} is not a git repository"
    if not _PR_CLI:
        return "no PR CLI configured (`pr_cli` is null in lanes.json)"
    return f"`{_PR_CLI}` failed or is not installed"


def _pr_index():
    """{number: {state, title, headRefName, mergedAt}} for every PR, or None when it could not be
    fetched. None is load-bearing: callers must report SKIPPED, never 'nothing merged'."""
    if "v" in _PR_INDEX_CACHE:
        return _PR_INDEX_CACHE["v"]
    idx = None
    if _PR_CLI and _repo_ready():
        try:
            raw = subprocess.run(
                [_PR_CLI, "pr", "list", "--state", "all", "--limit", "1000", "--json",
                 "number,state,title,headRefName,mergedAt"],
                cwd=_REPO, capture_output=True, text=True, timeout=60).stdout
            idx = {p["number"]: p for p in json.loads(raw)}
        except Exception:
            idx = None
    _PR_INDEX_CACHE["v"] = idx
    return idx


def _open_prs():
    if not (_PR_CLI and _repo_ready()):
        return None
    try:
        raw = subprocess.run(
            [_PR_CLI, "pr", "list", "--state", "open", "--json",
             "number,title,createdAt,mergeable,isDraft,headRefName"],
            cwd=_REPO, capture_output=True, text=True, timeout=60).stdout
        return json.loads(raw)
    except Exception:
        return None


def _branch_merged(name):
    """True only when <name> is provably an ancestor of the trunk. An unknown branch is False —
    never an assumption in either direction."""
    if not _repo_ready():
        return False
    for ref in (f"origin/{name}", name):
        try:
            if subprocess.run(["git", "merge-base", "--is-ancestor", ref, _TRUNK],
                              cwd=_REPO, capture_output=True, timeout=20).returncode == 0:
                return True
        except Exception:
            continue
    return False


def _shipped_evidence(f, pr_index):
    """(shipped, evidence) from HARD references only — a merged PR number, or a branch provably
    merged into the trunk. Fuzzy title similarity is deliberately NOT a closing signal here; it
    stays a warning in `reconcile`, where a human decides. Pure; never writes."""
    art = (f.get("artifact") or "").strip()
    if not art:
        return False, None
    if pr_index is not None:
        for m in _PR_REF_RE.finditer(art):
            pr = pr_index.get(int(m.group(1)))
            if pr and pr.get("state") == "MERGED":
                return True, (f"PR #{pr['number']} merged {(pr.get('mergedAt') or '')[:10]} — "
                              f"{pr['title'][:60]}")
    for m in _BRANCH_REF_RE.finditer(art):
        br = m.group(1).rstrip(",.;")
        if _branch_merged(br):
            return True, f"branch {br} is merged into {_TRUNK}"
    return False, None


def _shipped_scan(items, pr_index):
    out = []
    for f in items.values():
        if (f.get("status") or "").lower() in ("done", "dropped", "resolved"):
            continue
        # An incident is never closed by a merged PR. Shipping code does not make a system
        # healthy; only the check that raised the incident, reporting health, can close it.
        if (f.get("kind") or "work").lower() == "incident":
            continue
        ok, ev = _shipped_evidence(f, pr_index)
        if ok:
            out.append((f, ev))
    return sorted(out, key=lambda t: t[0]["_id"])


def _ground_truth_conflicts(f):
    """(checked, hits) — open PRs that may already cover this item; the anti-rebuild guard.

    The two return values are NOT interchangeable and collapsing them is a real bug, caught while
    porting this: "I checked and found nothing" and "I could not check" were both an empty list,
    so the caller either refused on every host without a PR CLI or silently trusted a check that
    never ran. Neither is acceptable, so inability-to-check is now its own value. A hit is a
    WARNING for a human to look at — a fuzzy match stops a pick, and never closes anything.
    """
    prs = _open_prs()
    if prs is None:
        return False, []
    toks = {t for t in re.split(r"[-_]", f["_slug"]) if len(t) > 3} - _STOP_SURFACE
    hits = []
    for p in prs:
        hay = f"{p['title']} {p['headRefName']}".lower()
        if f["_slug"] in hay or (toks and sum(1 for t in toks if t in hay)
                                 >= max(2, len(toks) - 1)):
            hits.append(f"open PR #{p['number']} '{p['title'][:60]}' may already cover this")
    for m in _PR_REF_RE.finditer(f.get("artifact", "") or ""):
        hits.append(f"artifact already points at PR #{m.group(1)} — check its state first")
    return True, hits


# ───────────────────────────────────────────────────────────────────────────────── item commands

def cmd_add(args):
    key = _lane_key(args.lane)
    if args.origin not in _ORIGINS:
        sys.exit(f"--origin must be one of {_ORIGINS}")
    if args.tier not in _TIERS:
        sys.exit(f"--tier must be one of {_TIERS}")
    kind = args.kind or "work"
    if kind not in _KINDS:
        sys.exit(f"--kind must be one of {_KINDS}")
    incident = kind == "incident"
    if incident and args.origin == "me":
        sys.exit("REFUSED: an incident is a machine observation, not your decision — file it "
                 "with --origin monitor. (A self-filed incident would auto-approve itself.)")

    slug = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")[:48]
    item_id = f"lane-item-{key.lower()}-{slug}"
    now = _now()

    # WHO FILED IT decides whether it needs your yes.
    #
    # Your own add IS the approval — asking you to approve what you just filed is a gate with
    # nothing on the other side of it. An agent-filed item at a tier you have pre-cleared enters
    # approved too, and the basis is written into the note so "why did nobody click this?" has a
    # re-checkable answer months later.
    #
    # The argument against widening this any further: approval is only dispatch-eligibility, so
    # auto-approving does not SHIP anything. But it does drop the item off `needs-you`, and the
    # bucket you most want to see is exactly the bucket that looks routine at intake. Keep
    # `agent_auto_approve_tiers` narrow.
    auto = (args.origin == "me" and not incident) or (
        args.origin == "agent" and not incident and args.tier.lower() in _AUTO_TIERS)

    fields = {
        "lane": key,
        "status": "open" if incident else ("approved" if auto else "proposed"),
        "origin": args.origin, "tier": args.tier, "size": args.size, "what": args.what,
        "done_means": args.done or "(define at framing)", "surfaces": args.surfaces or "",
        "kind": kind,
    }
    if incident:
        sev = args.severity or "warn"
        if sev not in _SEVERITIES:
            sys.exit(f"--severity must be one of {_SEVERITIES}")
        raw = (args.raw_evidence or "").strip()
        if not raw:
            sys.exit("REFUSED: --raw-evidence is required for an incident. It must carry the "
                     "verbatim check output, not a summarized verdict — the next reader has to "
                     "be able to re-derive the claim instead of trusting it.")
        prior = _parse_fields((_try_get(item_id) or {}).get("body", ""), _ITEM_FIELD_RE)
        fields["severity"] = sev
        fields["raw_evidence"] = raw.replace("\n", " ⏎ ")
        # first_seen survives a re-raise, so a standing failure shows its true age instead of
        # resetting to "0d" on every sweep.
        fields["first_seen"] = prior.get("first_seen") or _iso(now)
        fields["resolved_at"] = ""
    if auto:
        ttl = _TTL_DAYS.get(key[0], 14)
        fields.update({
            "approved_by": "self" if args.origin == "me" else f"auto:{args.origin}+{args.tier}",
            "approved_at": _iso(now),
            "approved_until": _iso(now + timedelta(days=ttl)),
            "evidence": (f"operator-add: filed by you {now.date()}" if args.origin == "me"
                         else f"note: tier {args.tier} + origin {args.origin} is pre-cleared "
                              f"in lanes.json ({now.date()})"),
            "scope_hash": _scope_hash(args.what, fields["done_means"]),
        })
    if args.blocked_on:
        fields["blocked_on"] = args.blocked_on
    if args.accel:
        fields["accel"] = args.accel

    prior_note = _try_get(item_id)
    prior_status = _parse_fields((prior_note or {}).get("body", ""),
                                 _ITEM_FIELD_RE).get("status", "")
    body = _set_fields("Lane work item.\n\n", fields, ITEM_FIELDS, _ITEM_FIELD_RE)
    _put(item_id, f"LANE ITEM {key} — {args.title}", body, ["lane-item", "lane-board"],
         prior_note)
    back = _parse_fields(store.get(item_id)["body"], _ITEM_FIELD_RE)
    if incident:
        print(f"RAISED {item_id} · {back.get('severity')} · status {back.get('status')} "
              f"· first_seen {back.get('first_seen')} · read-back OK · NOT an approval request")
    else:
        tail = (" · auto-approved; no click needed" if auto else " · AWAITS YOUR APPROVAL")
        verb = "REPLACED" if prior_note else "ADDED"
        print(f"{verb} {item_id} · status {back.get('status')} · origin {args.origin} "
              f"· tier {args.tier} · read-back OK{tail}")
        if prior_status == "approved" and not auto:
            # A re-add is a rewrite, so it discards the old approval rather than carrying it onto
            # different work. Announced, because a silently-revoked yes is indistinguishable from
            # a lost one, and the two call for opposite responses.
            print("  NOTE: this replaced an item that was already approved. The approval did not "
                  "carry over — it covered the previous wording, not this one.")


def cmd_approve(args):
    if _unattended() and not args.force:
        sys.exit("REFUSED: an unattended session cannot approve lane items — that gate exists "
                 "precisely so an agent cannot walk through it.\n"
                 "  Approve from an interactive session, relaying the yes as "
                 "--evidence \"quote: ...\".")
    if not args.evidence or not _EVIDENCE_RE.match(args.evidence):
        sys.exit("REFUSED: --evidence is required and must start with one of "
                 "operator-add:|session:|cmd:|pr:|quote:|note: — approval is recorded, "
                 "never inferred.")
    items = _load_items()
    f = _find_item(items, args.item)
    if (f.get("kind") or "work").lower() == "incident":
        sys.exit(f"REFUSED: {f['_id']} is an incident. Incidents resolve on evidence of health, "
                 f"not on approval — use `lanes resolve`.")
    if (f.get("status") or "").lower() not in ("proposed", "approved"):
        sys.exit(f"{f['_id']} is {f.get('status')} — only proposed items are approved.")
    now = _now()
    ttl = _TTL_DAYS.get(f["_lane"][0], 14)
    body = _set_fields(f["_note"]["body"], {
        "status": "approved", "approved_by": args.by or "operator", "approved_at": _iso(now),
        "approved_until": _iso(now + timedelta(days=ttl)), "evidence": args.evidence,
        "scope_hash": _scope_hash(f.get("what", ""), f.get("done_means", ""))},
        ITEM_FIELDS, _ITEM_FIELD_RE)
    _item_put(f, body)
    ok = _parse_fields(store.get(f["_id"])["body"], _ITEM_FIELD_RE).get("status") == "approved"
    print(f"APPROVED {f['_id']} · evidence {args.evidence} · fresh until "
          f"{_iso(now + timedelta(days=ttl))} · read-back {'OK' if ok else 'FAILED'}")
    if not ok:
        sys.exit(1)
    if f.get("blocked_on"):
        # Approving is not unblocking. They are separate on purpose — a yes to the work does not
        # make an external dependency go away — but an approved item that stays blocked is still
        # not pickable, and saying nothing here is how that becomes a mystery.
        print(f"  NOTE: still blocked — {f['blocked_on']}\n"
              f"  It will not be pickable until you clear it:  "
              f"lanes item-set {f['_id']} --blocked-on ''")


def cmd_resolve(args):
    """Close an incident on POSITIVE evidence that the condition cleared.

    Deliberately not the inverse of `add`. A check that has gone blind, or a unit that was
    renamed or deleted, "recovers" by simply disappearing from the failure list — and the board
    then affirmatively says all-clear when it should be saying nothing. So resolution demands
    evidence of HEALTH, not absence of failure.
    """
    items = _load_items()
    f = _find_item(items, args.item)
    if (f.get("kind") or "work").lower() != "incident":
        sys.exit(f"{f['_id']} is kind={f.get('kind') or 'work'} — resolve is for incidents. "
                 f"Work items close via `reconcile` or `item-set --status done`.")
    if (f.get("status") or "").lower() == "resolved":
        print(f"NOOP {f['_id']} already resolved at {f.get('resolved_at')}")
        return
    raw = (args.raw_evidence or "").strip()
    if not raw:
        sys.exit("REFUSED: --raw-evidence is required. It must carry the verbatim check output "
                 "showing the condition CLEARED. Absence of a failure is not evidence of health: "
                 "a deleted or renamed check also stops appearing in a failure list.")
    now = _now()
    body = _set_fields(f["_note"]["body"], {
        "status": "resolved", "resolved_at": _iso(now), "raw_evidence": raw.replace("\n", " ⏎ "),
        "closed_by": f"resolve:{args.by or 'monitor'}:{now.date()}"}, ITEM_FIELDS, _ITEM_FIELD_RE)
    _item_put(f, body)
    back = _parse_fields(store.get(f["_id"])["body"], _ITEM_FIELD_RE)
    if back.get("status") != "resolved":
        sys.exit(f"READ-BACK FAILED for {f['_id']} — still {back.get('status')}")
    print(f"RESOLVED {f['_id']} · at {back.get('resolved_at')} · read-back OK")


def cmd_triage(args):
    """Attach an ADVISORY note to an open incident — the only write an automated triage layer gets.

    It cannot open an item (that would be a second unread channel), cannot resolve one (only
    positive health evidence closes an incident), and cannot change severity — a model's opinion
    must not be able to escalate or de-escalate an alarm it is merely commenting on.
    """
    items = _load_items()
    f = _find_item(items, args.item)
    if (f.get("kind") or "work").lower() != "incident":
        sys.exit(f"REFUSED: {f['_id']} is not an incident — triage annotates incidents only.")
    if (f.get("status") or "").lower() != "open":
        sys.exit(f"REFUSED: {f['_id']} is {f.get('status')} — only open incidents are triaged.")
    body = _set_fields(f["_note"]["body"], {"triage": f"{_iso(_now())} · {args.note.strip()}"},
                       ITEM_FIELDS, _ITEM_FIELD_RE)
    _item_put(f, body)
    if not _parse_fields(store.get(f["_id"])["body"], _ITEM_FIELD_RE).get("triage"):
        sys.exit(f"READ-BACK FAILED for {f['_id']} — triage not stored")
    print(f"TRIAGED {f['_id']} · read-back OK · ADVISORY (nothing was run)")


def cmd_item_set(args):
    items = _load_items()
    f = _find_item(items, args.item)
    updates = {}
    if args.artifact:
        updates["artifact"] = args.artifact
    if args.status:
        if args.status not in _STATUSES:
            sys.exit(f"--status must be one of {_STATUSES}")
        if args.status == "approved":
            sys.exit("use `approve` (evidence-gated) — item-set cannot approve.")
        if args.status in ("done", "dropped") and not (args.evidence or args.artifact
                                                       or f.get("artifact")):
            sys.exit("done/dropped needs --evidence or an artifact pointer (a PR or commit) — "
                     "closure is recorded against ground truth, never asserted.")
        updates["status"] = args.status
    for attr in ("blocked_on", "accel", "evidence", "surfaces"):
        if getattr(args, attr, None) is not None:
            updates[attr] = getattr(args, attr)
    # Revising scope is deliberately allowed and deliberately NOT re-approving. The stored
    # scope_hash stops matching, the board flags SCOPE CHANGED, and the item drops out of `pick`
    # until somebody says yes to what it now says. Silently carrying an old approval across a
    # rewritten scope is the failure this guards.
    if args.what is not None:
        updates["what"] = args.what
    if args.done is not None:
        updates["done_means"] = args.done
    if not updates:
        sys.exit("nothing to set")
    body = _set_fields(f["_note"]["body"], updates, ITEM_FIELDS, _ITEM_FIELD_RE)
    _item_put(f, body)
    print(f"SET {f['_id']} · " + " · ".join(f"{k}={v or '(cleared)'}" for k, v in updates.items()))
    if (("what" in updates or "done_means" in updates)
            and (f.get("status") or "").lower() == "approved"):
        print("  NOTE: the scope changed, so the existing approval no longer covers it. The "
              "board will flag SCOPE CHANGED and `pick` will skip it until it is approved again.")
    if updates.get("status") in ("done", "dropped"):
        print(f"  release the lane lock if you hold it:  lanes release {f['_lane']}")


def cmd_queue(args):
    now = _now()
    items = _load_items()
    if args.json:
        out = []
        for f in items.values():
            out.append({k: v for k, v in f.items() if not k.startswith("_") or k == "_id"})
        print(json.dumps(sorted(out, key=lambda r: r["_id"]), indent=2, default=str))
        return
    lanes = _load_lanes()
    by_lane = {}
    for f in items.values():
        by_lane.setdefault(f["_lane"], []).append(f)
    keys = sorted(by_lane, key=lambda k: _lane_sort_key(k, lanes))
    if args.lane:
        want = _lane_key(args.lane)
        keys = [k for k in keys if k == want]
    icons = {"proposed": "🟡", "approved": "🟢", "in-progress": "🔒", "done": "✅",
             "dropped": "🗑", "open": "🔴", "resolved": "✅"}
    eligible_n = pending = 0
    print(f"**Lane queue** @ {now:%Y-%m-%d %H:%M}Z — a live view. The authority on whether an "
          f"item is workable is `pick`'s ground-truth check, never this render.\n")
    for key in keys:
        title = (lanes.get(key) or {}).get("title", "")
        print(f"### {key}" + (f" — {title}" if title else ""))
        for f in sorted(by_lane[key], key=lambda x: x.get("_created", "")):
            status = (f.get("status") or "?").lower()
            ok, badges = _item_flags(f, now)
            eligible_n += ok
            pending += status == "proposed"
            line = (f"- {icons.get(status, '·')} `{f['_id']}` "
                    f"[{f.get('origin', '?')}·{f.get('tier', '?')}] {f.get('what', '')[:110]}")
            if status == "approved" and f.get("approved_until"):
                left = _parse_ts(f["approved_until"])
                if left:
                    line += f" · fresh {max(0, (left - now).days)}d"
            if f.get("artifact"):
                line += f" · {f['artifact']}"
            print(line)
            for b in badges:
                print(f"    ⚠ {b}")
        print()
    print(f"eligible now: {eligible_n} · awaiting your approval: {pending}")


def cmd_pick(args):
    """Claim the top eligible item on the best free lane, in one step.

    Eligibility is computed here and validated against version control before anything is
    claimed, because the queue is a cache and the repo is the authority. An item whose work is
    already sitting in an open PR is the single most expensive thing to start.
    """
    now = _now()
    items = _load_items()
    lanes = _load_lanes()
    locks = _load_locks()

    if args.lane:
        wanted = [_lane_key(args.lane)]
    else:
        series = (args.series or "").upper() or next(
            (s["key"].upper() for s in CFG["series"] if s.get("contended")), SERIES[0])
        if series not in SERIES:
            sys.exit(f"unknown series '{series}' — in force: {', '.join(SERIES)}")
        wanted = [k for k in lanes if k[0] == series]
    wanted.sort(key=lambda k: _lane_sort_key(k, lanes))

    candidates = []
    for pos, key in enumerate(wanted):
        lane = lanes.get(key, {})
        if (lane.get("disposition") or "open").lower() in ("parked", "closed"):
            continue
        lock = locks.get(key)
        if lock and _held(lock, now)[0]:
            continue                                    # one holder per lane
        lane_items = [f for f in items.values() if f["_lane"] == key]
        if any((f.get("status") or "").lower() == "in-progress" for f in lane_items):
            continue                                    # one item in flight per lane
        for f in sorted(lane_items, key=lambda x: x.get("_created", "")):
            ok, badges = _item_flags(f, now)
            if ok or (args.force and (f.get("status") or "").lower() == "approved"):
                candidates.append((pos, f))
                break
    if not candidates:
        sys.exit("no eligible item on a free lane. Try `lanes queue` — approvals may be stale, "
                 "or every lane may be held.")

    pos, f = sorted(candidates, key=lambda c: c[0])[0]
    checked, conflicts = _ground_truth_conflicts(f)
    if not checked:
        why = _why_no_pr()
        # Loud, and then it proceeds. Refusing here would make the tool unusable on any host
        # without a PR CLI, and it would also be a lie: nothing was found because nothing was
        # looked at, which is a different statement from "there is a conflict".
        print(f"⚠ ground-truth pre-flight NOT RUN ({why}) — check by hand that no open PR "
              f"already covers this before you start.")
    if conflicts and not args.force:
        for c in conflicts:
            print(f"CONFLICT: {c}")
        sys.exit(f"REFUSED: the ground-truth pre-flight hit on {f['_id']}. Reconcile first — the "
                 f"queue is a cache, the repo is the authority. Use --force only after checking "
                 f"the hits are unrelated.")
    if args.dry_run:
        print(f"[dry-run] would pick {f['_id']} on {f['_lane']} (tier {f.get('tier')}, "
              f"size {f.get('size', 'single-task')}); "
              f"pre-flight: {'not run' if not checked else (conflicts or 'clean')}")
        return

    holder = args.holder or _holder_id()
    cmd_claim(argparse.Namespace(
        lane=f["_lane"], task=f"{f['_id']}: {f.get('what', '')[:80]}",
        surfaces=f.get("surfaces") or f["_slug"], holder=holder, hours=args.hours,
        force=args.force, dry_run=False))                # exits loudly if the lock is refused
    body = _set_fields(f["_note"]["body"],
                       {"status": "in-progress", "holder": holder,
                        "artifact": f.get("artifact") or "(set when the PR opens)"},
                       ITEM_FIELDS, _ITEM_FIELD_RE)
    _item_put(f, body)
    print(f"PICKED {f['_id']} · lane {f['_lane']} locked · tier {f.get('tier')}\n"
          f"  When the PR opens, record it so `reconcile` can close this by itself:\n"
          f"    lanes item-set {f['_id']} --artifact 'PR #<n>'")


# ──────────────────────────────────────────────────────────────────────────────────────── board

def _lane_icon(lane, lock, now):
    """A live hold wins over anything recorded. A stored 'held' with no live lock is a stale
    snapshot, and the board must never render a lock that is not actually held — that is how a
    coordination signal quietly becomes decoration."""
    if lock and _held(lock, now)[0]:
        return "🔒", "held"
    d = (lane.get("disposition") or "open").lower()
    return DISPO_ICON.get(d, "▫️"), d


def _dead_agent(f, locks, now):
    """An item claiming in-progress on a lane holding no live lock: a vanished agent, or a lease
    that expired out from under one. This is the anomaly the board exists to surface."""
    if (f.get("status") or "").lower() != "in-progress":
        return False
    lock = locks.get(f["_lane"])
    return not (lock and _held(lock, now)[0])


def cmd_board(args):
    now = _now()
    lanes = _load_lanes()
    locks = _load_locks()
    items = _load_items()

    # A lane that is held right now but was never defined still renders. A live lane must never
    # be invisible just because the paperwork is behind.
    keys = set(lanes) | {k for k, l in locks.items() if _held(l, now)[0]}
    keys |= {f["_lane"] for f in items.values()
             if (f.get("status") or "").lower() not in ("done", "dropped", "resolved")}
    ordered = sorted(keys, key=lambda k: _lane_sort_key(k, lanes))

    L = [f"**Lane board** — live from the store @ {now:%Y-%m-%d %H:%M}Z.  ",
         "One holder per lane. 🔒 means somebody is working it right now.\n"]

    for s in SERIES:
        rows = [k for k in ordered if k[0] == s]
        if not rows:
            continue
        L.append(f"### {s} — {SERIES_META[s]['label']}")
        L.append("| Lane | Status | What / where it stands |")
        L.append("|---|---|---|")
        # Closed lanes sort to the bottom of their series rather than out of the table, so a lane
        # you closed last month is still findable without a second command.
        rows.sort(key=lambda k: (lanes.get(k, {}).get("disposition") == "closed",))
        for key in rows:
            lane = lanes.get(key, {})
            lock = locks.get(key)
            icon, disp = _lane_icon(lane, lock, now)
            title = lane.get("title") or "_(no lane definition — `lanes lane-add`)_"
            status = f"{icon} {DISPO_PHRASE.get('held' if icon == '🔒' else disp, '')}"
            gate = _cell(_first_sentence(lane.get("gate", "")))
            if lock and _held(lock, now)[0]:
                lease = _parse_ts(lock.get("lease_until"))
                mins = int((lease - now).total_seconds() // 60) if lease else None
                who = (lock.get("holder") or "?").split("(")[0].strip()
                held = f"held by {who}" + (f", lease {mins}m left" if mins is not None else "")
                gate = f"{held} · {gate}" if gate else held
            elif lock and _held(lock, now)[1]:
                gate = ("⚠ lock is active but its lease EXPIRED (dead agent; treat as free) · "
                        + gate)
            L.append(f"| **{key}** {_cell(title, 46)} | {status} | {gate} |")
        L.append("")

    blocked_lanes = [(k, lanes[k]) for k in ordered
                     if k in lanes and lanes[k].get("blocked_on")]
    blocked_items = [f for f in items.values()
                     if f.get("blocked_on")
                     and (f.get("kind") or "work").lower() != "incident"
                     and (f.get("status") or "").lower() not in ("done", "dropped")]
    if blocked_lanes or blocked_items:
        L.append("### ⛔ Waiting on you")
        for k, lane in blocked_lanes:
            L.append(f"- **{k}** {_cell(lane.get('title', ''), 52)} — "
                     f"{_cell(lane['blocked_on'], 130)}")
        for f in blocked_items:
            L.append(f"- **{f['_lane']}** `{f['_id']}` — {_cell(f['blocked_on'], 130)}")
        L.append("")

    incidents = [f for f in items.values()
                 if (f.get("kind") or "work").lower() == "incident"
                 and (f.get("status") or "").lower() == "open"]
    if incidents:
        L.append(f"### 🔴 Open incidents ({len(incidents)}) — broken, not awaiting a decision")
        for f in incidents:
            L.append(f"- **{(f.get('severity') or 'warn').upper()}** `{f['_id']}` — "
                     f"{_cell(f.get('what', ''), 120)}")
        L.append("")

    dead = [f for f in items.values() if _dead_agent(f, locks, now)]
    if dead:
        L.append("### ⚠ In progress with nobody holding the lane")
        for f in dead:
            L.append(f"- `{f['_id']}` on {f['_lane']} — the holder is gone or its lease expired. "
                     f"Re-claim, or set it back: `lanes item-set {f['_id']} --status proposed`")
        L.append("")

    running = [(k, locks[k]) for k in ordered if k in locks and _held(locks[k], now)[0]]
    if running:
        L.append("**Running now:** " + ", ".join(
            f"{k} ({(l.get('holder') or 'held')})" for k, l in running) + ".")
    else:
        L.append("**Running now:** nothing is held.")
    waiting = sum(1 for f in items.values() if (f.get("status") or "").lower() == "proposed")
    L.append(f"**Queue:** {len(items)} item(s) · {waiting} awaiting your approval "
             f"· `lanes needs-you` for what is actually gated on you.")
    print("\n".join(L))


def cmd_show(args):
    key = _lane_key(args.lane)
    now = _now()
    lane = _load_lanes().get(key)
    lock = _load_locks().get(key)
    items = [f for f in _load_items().values() if f["_lane"] == key]
    if lane:
        print(f"# {key} — {lane.get('title', '')}")
        for k in ("rank", "disposition", "gate", "blocked_on", "accel"):
            if lane.get(k):
                print(f"  {k}: {lane[k]}")
    else:
        print(f"# {key} — (no lane definition; `lanes lane-add {key} --title \"...\"`)")
    print()
    if lock:
        held, stale = _held(lock, now)
        print(f"lock: {lock['_id']}  [{'HELD' if held else 'EXPIRED' if stale else 'free'}]")
        for k in LOCK_FIELDS:
            if lock.get(k):
                print(f"  {k}: {lock[k]}")
    else:
        print("lock: none")
    print()
    if items:
        print(f"items ({len(items)}):")
        for f in sorted(items, key=lambda x: x.get("_created", "")):
            print(f"  [{f.get('status', '?')}] {f['_id']} — {f.get('what', '')[:90]}")
    else:
        print("items: none")


def cmd_blocked(args):
    now = _now()
    lanes = _load_lanes()
    items = _load_items()
    rows = [(k, l.get("title", ""), l["blocked_on"], l.get("accel"))
            for k, l in lanes.items() if l.get("blocked_on")]
    rows += [(f["_lane"], f["_id"], f["blocked_on"], f.get("accel"))
             for f in items.values()
             if f.get("blocked_on") and (f.get("kind") or "work").lower() != "incident"
             and (f.get("status") or "").lower() not in ("done", "dropped")]
    if not rows:
        print("Nothing is blocked on you right now.")
        return
    print("### ⛔ Waiting on you")
    for i, (key, what, why, how) in enumerate(sorted(rows), 1):
        print(f"{i}. **{key}** {_cell(what, 60)} — {_cell(why, 140)}")
        print(f"   ▶ How to do it: {how}" if how else
              "   ▶ How to do it: _no steps recorded — that is itself a defect; every ⛔ ask "
              "should carry the exact command or click that clears it._")


# ────────────────────────────────────────────────────────────────────────── the operator surface

def _budget_line(items):
    """One line when the debt is over budget; '' when it is not.

    Advisory on purpose. A hard block gets gamed or switched off; a number rendered at the top of
    the surface you already read cannot be silently ignored.
    """
    awaiting = sum(1 for f in items.values()
                   if (f.get("status") or "").lower() == "proposed")
    over = []
    if awaiting > _BUDGET_AWAITING:
        over.append(f"awaiting-you {awaiting}/{_BUDGET_AWAITING}")
    try:
        n_follow = sum(1 for r in store.followups(tenant=TENANT) if r.get("state") == "open")
    except Exception:
        n_follow = None
    if n_follow is None:
        over.append("open follow-ups UNKNOWN (the store would not answer)")
    elif n_follow > _BUDGET_FOLLOWUPS:
        over.append(f"open follow-ups {n_follow}/{_BUDGET_FOLLOWUPS}")
    if not over:
        return ""
    return ("> 🧊 **Over budget** — " + " · ".join(over) + "\n>\n"
            "> Close something before you open something. The failure mode is never too little "
            "process; it is that nothing retires what the process creates.\n"
            "> Start with `lanes reconcile --apply`.")


def _pr_rot(stale_days=3):
    """Open PRs sorted by how long they have waited, each with a verdict — or None if unknown.

    Nothing else watches the clock. A tier model says WHO merges; it never says BY WHEN, so a PR
    that is nobody's emergency sits until it conflicts with the trunk and needs a rebase before
    anyone can merge it at all.
    """
    prs = _open_prs()
    if prs is None:
        return None
    now, out = _now(), []
    for p in prs:
        created = _parse_ts(p.get("createdAt"))
        age = (now - created).days if created else 0
        conflicting = p.get("mergeable") == "CONFLICTING"
        if conflicting:
            verdict = "🔴 ROTTED — conflicts with the trunk; needs a rebase before it can merge"
        elif age >= stale_days:
            verdict = f"🟠 waiting {age}d"
        else:
            verdict = f"🟢 {age}d"
        out.append({"number": p["number"], "title": p["title"], "age": age,
                    "draft": bool(p.get("isDraft")), "verdict": verdict,
                    "conflicting": conflicting})
    return sorted(out, key=lambda x: (-x["age"], x["number"]))


def cmd_needs_you(args):
    """Everything gated on you, each with the command that clears it, and nothing else.

    The reason this is one surface rather than a section of the board: what needs a person is
    otherwise scattered across the board, the PR list and whatever a given session happened to
    mention, so the only reliable way to find out is to ask. Sorted by whether an entry needs a
    DECISION or merely a COMMAND, because those are different kinds of attention and mixing them
    is what makes a queue unreadable.
    """
    now = _now()
    items = _load_items()
    lanes = _load_lanes()
    pr_index = _pr_index()
    shipped = {f["_id"]: ev for f, ev in _shipped_scan(items, pr_index)}

    def age(f):
        c = _parse_ts(f.get("_created"))
        return (now - c).days if c else 0

    print(f"# What needs you — {now:%Y-%m-%d %H:%M}Z\n")
    budget = _budget_line(items)
    if budget:
        print(budget + "\n")
    gates = 0

    incidents = [f for f in items.values()
                 if (f.get("kind") or "work").lower() == "incident"
                 and (f.get("status") or "").lower() == "open"]
    if incidents:
        rank = {"crit": 0, "warn": 1, "info": 2}
        incidents.sort(key=lambda f: (rank.get((f.get("severity") or "warn").lower(), 1),
                                      f.get("first_seen") or ""))
        print(f"## 🔴 Open incidents ({len(incidents)}) — already broken, not awaiting a yes\n")
        for f in incidents:
            seen = _parse_ts(f.get("first_seen"))
            if seen:
                d = now - seen
                # Hours below a day. "open 0d" on a twenty-hour-old CRIT reads as brand new,
                # which is exactly the wrong impression from the one field carrying urgency.
                span = f"{d.days}d" if d.days else f"{int(d.total_seconds() // 3600)}h"
            else:
                span = "?"
            print(f"- **{(f.get('severity') or 'warn').upper()}** · open {span} · `{f['_id']}`\n"
                  f"  {f.get('what', '')}")
            if f.get("raw_evidence"):
                ev = f["raw_evidence"]
                if " ⏎ " in ev:
                    print("  ▶ evidence:")
                    for ln in ev.split(" ⏎ "):
                        print(f"      {ln}")
                else:
                    print(f"  ▶ evidence: `{ev}`")
            if f.get("triage"):
                print(f"  🤖 triage (advisory — nothing was run): {f['triage']}")
        print("\n  These clear when the check that raised them reports health, not when you "
              "read them.\n  They are excluded from the awaiting-you budget by design.\n")

    blocked = [("item", f["_id"], f.get("what", ""), f["blocked_on"], f.get("accel"), age(f))
               for f in items.values()
               if f.get("blocked_on")
               and (f.get("kind") or "work").lower() != "incident"
               and (f.get("status") or "").lower() not in ("done", "dropped")]
    blocked += [("lane", k, l.get("title", ""), l["blocked_on"], l.get("accel"), 0)
                for k, l in lanes.items() if l.get("blocked_on")]
    if blocked:
        gates += 1
        print(f"## ⛔ Blocked on you ({len(blocked)}) — nothing moves until you act\n")
        for kind, ref, what, why, how, days in sorted(blocked, key=lambda r: -r[5]):
            print(f"- **`{ref}`**" + (f" · {days}d" if days else ""))
            print(f"  {_cell(what, 150)}")
            print(f"  ⛔ {_cell(why, 150)}")
            print(f"  ▶ **How to do it:** {how}" if how else
                  "  ▶ **How to do it:** _no steps recorded — that is itself a defect; every ⛔ "
                  "ask should carry the exact command or click that clears it._")
        print()

    rot = _pr_rot()
    if rot is None:
        why = _why_no_pr()
        print(f"## 🔀 Pull requests\n\n⚠ NOT CHECKED this run ({why}). Reported rather than "
              f"silently rendered as 'nothing waiting'.\n")
    elif rot:
        gates += 1
        stale = [p for p in rot if p["age"] >= 3 or p["conflicting"]]
        print(f"## 🔀 Open PRs ({len(rot)}) — {len(stale)} waiting three days or more\n")
        for p in rot:
            d = " *(draft)*" if p["draft"] else ""
            print(f"- {p['verdict']} · **#{p['number']}**{d} {p['title'][:76]}")
        print(f"\n  ▶ **How to do it:** merge from your PR host · rotted ones need "
              f"`git rebase {_TRUNK}` first\n")

    needs_yes = [f for f in items.values()
                 if (f.get("status") or "").lower() == "proposed" and f["_id"] not in shipped]
    if needs_yes:
        gates += 1
        print(f"## 🟡 Needs your yes ({len(needs_yes)}) — oldest first\n")
        for f in sorted(needs_yes, key=age, reverse=True):
            print(f"- **`{f['_id']}`** [{f.get('origin', '?')}·{f.get('tier', '?')}] · {age(f)}d")
            print(f"  {_cell(f.get('what', ''), 150)}")
        print("\n  ▶ **How to do it:** "
              "`lanes approve <id> --evidence \"operator-add: <why>\"`\n")

    # Deliberately last and visually separate. Mixing chores into the decision sections is what
    # makes an operator queue unreadable — these need a command, not a judgment.
    chores = []
    if shipped:
        chores.append((f"{len(shipped)} lane item(s) already shipped but still open on the board",
                       "lanes reconcile --apply"))
    dead = [f for f in items.values() if _dead_agent(f, _load_locks(), now)]
    if dead:
        chores.append((f"{len(dead)} item(s) in progress with nobody holding the lane",
                       "lanes board"))
    if chores:
        print(f"## 🧹 Housekeeping ({len(chores)}) — a command, not a decision\n")
        for what, how in chores:
            print(f"- {what}\n  ▶ `{how}`")
        print()

    if not gates and not chores and not incidents:
        print("Nothing is gated on you right now.\n")
    print("---")
    print("_Everything gated on you is above. Anything not here is not yours to unblock._")


def cmd_reconcile(args):
    """Close items whose work provably shipped. Dry run unless --apply.

    This is the mechanical half of "lane state is a function of the repo, not of an agent
    remembering to update it". It acts only on the hard evidence `_shipped_evidence` returns, and
    it writes that evidence into the note, so the closure stays auditable and re-checkable rather
    than being an assertion that something is done.
    """
    now = _now()
    items = _load_items()
    pr_index = _pr_index()

    print(f"**Lane reconcile** @ {now:%Y-%m-%d %H:%M}Z "
          f"({'DRY RUN — nothing written; add --apply' if not args.apply else 'APPLYING'})\n")

    if pr_index is None:
        print(f"⚠ PR-reference detection SKIPPED this run ({_why_no_pr()}).")
        print("  Branch-ancestry detection still ran where a repo is configured. This is")
        print("  reported rather than silently treated as 'nothing merged' — an empty result")
        print("  there is indistinguishable from a clean board.\n")

    shipped = _shipped_scan(items, pr_index)
    if not shipped:
        print("No open item carries hard shipped evidence. Nothing to close.")
    else:
        print(f"### Shipped but still open ({len(shipped)})\n")
        for f, ev in shipped:
            print(f"- `{f['_id']}` [{f.get('status')}] — {ev}")
            if args.apply:
                body = _set_fields(f["_note"]["body"],
                                   {"status": "done",
                                    "closed_by": f"reconcile {now:%Y-%m-%dT%H:%MZ} · {ev}"},
                                   ITEM_FIELDS, _ITEM_FIELD_RE)
                _item_put(f, body)
                back = _parse_fields(store.get(f["_id"])["body"], _ITEM_FIELD_RE)
                print("    ✅ closed (status: done · closed_by recorded)" if
                      back.get("status") == "done" else
                      "    ⚠ READ-BACK FAILED — still " + str(back.get("status")))
        print()

    # Everything below is SURFACED, never decided. Slug overlap is a hint; a hint that closes
    # things is an inference wearing the clothes of a fact.
    rotting, dupes = [], []
    done_by_slug = {f["_slug"]: f for f in items.values()
                    if (f.get("status") or "").lower() == "done"}
    for f in items.values():
        status = (f.get("status") or "").lower()
        if status in ("done", "dropped", "resolved"):
            continue
        created = _parse_ts(f.get("_created"))
        if status == "proposed" and created and (now - created).days > _PROPOSED_ROT_DAYS:
            rotting.append(((now - created).days, f))
        toks = {t for t in re.split(r"[-_]", f["_slug"]) if len(t) > 4} - _STOP_SURFACE
        for dslug, df in done_by_slug.items():
            dtoks = {t for t in re.split(r"[-_]", dslug) if len(t) > 4} - _STOP_SURFACE
            if toks and dtoks and len(toks & dtoks) >= max(3, min(len(toks), len(dtoks)) - 1):
                dupes.append((f, df))
                break

    if dupes:
        print(f"### Possible duplicates of a closed item ({len(dupes)}) — NOT auto-closed\n")
        print("Slug overlap is a hint, not evidence. Confirm, then close by hand:")
        print("  `lanes item-set <id> --status done --evidence '<why>'`\n")
        for f, df in dupes:
            print(f"- `{f['_id']}` [{f.get('status')}]")
            print(f"    overlaps closed `{df['_id']}` ({(df.get('artifact') or '')[:60]})")
        print()

    if rotting:
        print(f"### Rotting in `proposed` for over {_PROPOSED_ROT_DAYS}d "
              f"({len(rotting)}) — oldest first\n")
        for days, f in sorted(rotting, reverse=True, key=lambda t: t[0])[:15]:
            print(f"- {days:3d}d  `{f['_id']}` [{f.get('origin', '?')}] {f.get('what', '')[:70]}")
        print()

    print(f"Totals: {len(items)} items · {len(shipped)} closable now · "
          f"{len(dupes)} possible duplicates · {len(rotting)} rotting proposals")
    if shipped and not args.apply:
        print("\nRe-run with --apply to close the shipped ones.")


def cmd_priorities(args):
    """Render the priority register — the ranked list of everything in the mix.

    The register is a note you stack by hand, deliberately. An automatically-ordered list is a
    list nobody argues with, and the argument is the whole value: ranking forces you to say what
    a thing IS before you say when it happens. The badges below are prompts for the next restack,
    not gates.
    """
    now = _now()
    n = _try_get(REGISTER_ID)
    if not n:
        sys.exit(f"no `{REGISTER_ID}` note in the store yet.\n"
                 f"  Create one whose body holds lines of the form:\n"
                 f"    1. [security] ref:<lane-item-id> — one line of what it is\n"
                 f"  Classes, highest first: {' → '.join(_RANK_CLASSES)}")
    body = _normalize_body(n.get("body", ""))
    entries = [(int(m.group(1)), m.group(2).lower(), m.group(3), m.group(4).strip())
               for m in (_REG_ENTRY_RE.match(l) for l in body.splitlines()) if m]
    updated = _parse_ts(n.get("frontmatter", {}).get("updated", ""))
    age = (now - updated).days if updated else None
    head = f"**Priority register** — the ranked list of everything in the mix @ {now:%Y-%m-%d}"
    if age is not None:
        head += f" · last stacked {age}d ago"
    print(head + "\n")

    items = _load_items()
    ranked, warnings, last_ci = set(), [], -1
    for rank, cls, ref, rest in entries:
        ranked.add(ref)
        print(f"{rank:>2}. [{cls}] {rest}" + (f"\n      ref:{ref}" if args.refs else ""))
        ci = _RANK_CLASSES.index(cls) if cls in _RANK_CLASSES else None
        if ci is None:
            warnings.append(f"#{rank}: unknown class '{cls}' "
                            f"(the order is {' → '.join(_RANK_CLASSES)})")
        elif ci < last_ci:
            warnings.append(f"#{rank}: [{cls}] sits below [{_RANK_CLASSES[last_ci]}] — out of "
                            f"class order. A deliberate override, or time to restack?")
        if ci is not None:
            last_ci = max(last_ci, ci)
        f = items.get(ref)
        if f and (f.get("status") or "").lower() in ("done", "dropped") and not f.get("blocked_on"):
            warnings.append(f"#{rank}: {ref} is {f.get('status')} — drop it at the next restack")

    unranked = sorted((f for i, f in items.items()
                       if (f.get("status") or "").lower() in
                       ("proposed", "approved", "in-progress") and i not in ranked),
                      key=lambda x: x.get("_created", ""))
    if warnings or unranked or (age is not None and age > _REGISTER_STALE_DAYS):
        print()
    if age is not None and age > _REGISTER_STALE_DAYS:
        print(f"⚠ last stacked {age}d ago — a restack is due (diff it against `lanes queue`)")
    for w in warnings:
        print(f"⚠ {w}")
    if unranked:
        print("**Unranked — needs a place in the list:**")
        for f in unranked:
            print(f"- `{f['_id']}` [{f.get('status', '?')}] {_cell(f.get('what', ''), 100)}")
    print(f"\nClasses, highest first: {' → '.join(_RANK_CLASSES)} "
          f"· a class is by nature, not by mood · register note: {REGISTER_ID}")


def cmd_config(args):
    print(f"config file : {CONFIG_PATH}"
          f"{'' if CONFIG_PATH.exists() else '  (absent — built-in defaults in force)'}")
    print(f"store tenant: {TENANT}")
    print(f"repo        : {_REPO or '(none — ground-truth checks will report SKIPPED)'}")
    print(f"trunk       : {_TRUNK}")
    print(f"PR CLI      : {_PR_CLI or '(none)'}"
          f"{'' if not _PR_CLI or _repo_ready() else '  (no repo, so unused)'}")
    print(f"origins     : {', '.join(_ORIGINS)}")
    print(f"auto-approve: origin `me` always; origin `agent` at tier(s) "
          f"{', '.join(sorted(_AUTO_TIERS)) or '(none)'}")
    print(f"unattended  : {'YES — approve is refused' if _unattended() else 'no'}"
          f"   (set HARNESS_UNATTENDED=1)")
    print("series      :")
    for s in CFG["series"]:
        print(f"  {s['key'].upper():<3} {s['label']}"
              f"   [{'in contention' if s.get('contended') else 'background'}, "
              f"approval TTL {s.get('ttl_days', 14)}d]")
    print(f"budget      : {_BUDGET_AWAITING} awaiting you · {_BUDGET_FOLLOWUPS} open follow-ups")


def main():
    ap = argparse.ArgumentParser(
        prog="lanes",
        description="The parallel-work board: what is running, what is queued, what waits on you.")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("board", help="the whole picture (default)")
    sp = sub.add_parser("show", help="one lane in full"); sp.add_argument("lane")
    sub.add_parser("blocked", help="only what is waiting on you")
    sub.add_parser("config", help="print the configuration in force")

    la = sub.add_parser("lane-add", help="define a lane")
    la.add_argument("lane")
    la.add_argument("--title", required=True)
    la.add_argument("--rank", type=int, default=99, help="order within its series (lower first)")
    la.add_argument("--disposition", default="open", choices=_DISPOSITIONS)
    la.add_argument("--gate", default=None, help="one line: where this lane stands")
    la.add_argument("--blocked-on", dest="blocked_on", default=None)
    la.add_argument("--accel", default=None, help="the exact command or click that unblocks it")
    la.add_argument("--force", action="store_true", help="overwrite an existing definition")

    ls = sub.add_parser("lane-set", help="change a lane definition")
    ls.add_argument("lane")
    ls.add_argument("--title", default=None)
    ls.add_argument("--rank", type=int, default=None)
    ls.add_argument("--disposition", default=None, choices=_DISPOSITIONS)
    ls.add_argument("--gate", default=None)
    ls.add_argument("--blocked-on", dest="blocked_on", default=None)
    ls.add_argument("--accel", default=None)

    cl = sub.add_parser("claim", help="take a lane (refused on a live hold or surface collision)")
    cl.add_argument("lane")
    cl.add_argument("--task", required=True)
    cl.add_argument("--surfaces", required=True,
                    help="comma/space-separated files, dirs or systems this work will touch")
    cl.add_argument("--holder", default=None)
    cl.add_argument("--hours", type=float, default=2.0, help="lease length (default 2)")
    cl.add_argument("--force", action="store_true")
    cl.add_argument("--dry-run", action="store_true")

    hb = sub.add_parser("heartbeat", help="extend the lease on a lane you hold")
    hb.add_argument("lane"); hb.add_argument("--hours", type=float, default=2.0)
    rl = sub.add_parser("release", help="give a lane back"); rl.add_argument("lane")

    ad = sub.add_parser("add", help="put a work item (or an incident) into a lane")
    ad.add_argument("lane")
    ad.add_argument("--title", required=True)
    ad.add_argument("--what", required=True, help="one line: the work")
    ad.add_argument("--done", default=None, help="one line: what done means")
    ad.add_argument("--origin", required=True, choices=_ORIGINS)
    ad.add_argument("--tier", default="green", choices=_TIERS)
    ad.add_argument("--size", default="single-task", choices=("single-task", "sprint"))
    ad.add_argument("--surfaces", default="")
    ad.add_argument("--blocked-on", dest="blocked_on", default=None)
    ad.add_argument("--accel", default=None)
    ad.add_argument("--kind", default="work", choices=_KINDS,
                    help="`incident` surfaces on needs-you WITHOUT entering the approval queue "
                         "(requires --origin monitor and --raw-evidence)")
    ad.add_argument("--severity", default=None, choices=_SEVERITIES, help="incidents only")
    ad.add_argument("--raw-evidence", dest="raw_evidence", default=None,
                    help="incidents only: verbatim check output, never a summarized verdict")

    apv = sub.add_parser("approve", help="your gate: proposed → approved (evidence required)")
    apv.add_argument("item")
    apv.add_argument("--evidence", required=True,
                     help="prefixed operator-add:|session:|cmd:|pr:|quote:|note:")
    apv.add_argument("--by", default=None)
    apv.add_argument("--force", action="store_true", help="override the unattended refusal")

    rs = sub.add_parser("resolve", help="close an incident on evidence it cleared")
    rs.add_argument("item")
    rs.add_argument("--raw-evidence", dest="raw_evidence", required=True,
                    help="verbatim output showing HEALTH, not merely the absence of a failure")
    rs.add_argument("--by", default=None)

    tg = sub.add_parser("triage", help="attach an advisory note to an open incident")
    tg.add_argument("item"); tg.add_argument("--note", required=True)

    qu = sub.add_parser("queue", help="every item, live")
    qu.add_argument("lane", nargs="?", default=None)
    qu.add_argument("--json", action="store_true")

    pk = sub.add_parser("pick", help="claim the top eligible item on the best free lane")
    pk.add_argument("--lane", default=None)
    pk.add_argument("--series", default=None)
    pk.add_argument("--holder", default=None)
    pk.add_argument("--hours", type=float, default=2.0)
    pk.add_argument("--force", action="store_true")
    pk.add_argument("--dry-run", action="store_true")

    it = sub.add_parser("item-set", help="update an item")
    it.add_argument("item")
    it.add_argument("--artifact", default=None, help="where the work landed, e.g. 'PR #412'")
    it.add_argument("--status", default=None)
    it.add_argument("--blocked-on", dest="blocked_on", default=None)
    it.add_argument("--accel", default=None)
    it.add_argument("--surfaces", default=None)
    it.add_argument("--evidence", default=None)
    it.add_argument("--what", default=None,
                    help="revise the scope; this INVALIDATES an existing approval")
    it.add_argument("--done", default=None,
                    help="revise what done means; this INVALIDATES an existing approval")

    sub.add_parser("needs-you", help="everything gated on you, each with its command")
    rc = sub.add_parser("reconcile", help="close items whose work provably shipped")
    rc.add_argument("--apply", action="store_true", help="write the closures (default: dry run)")
    pri = sub.add_parser("priorities", help="the ranked register")
    pri.add_argument("--refs", action="store_true")

    args = ap.parse_args()
    {"board": cmd_board, "show": cmd_show, "blocked": cmd_blocked, "config": cmd_config,
     "lane-add": cmd_lane_add, "lane-set": cmd_lane_set,
     "claim": cmd_claim, "heartbeat": cmd_heartbeat, "release": cmd_release,
     "add": cmd_add, "approve": cmd_approve, "resolve": cmd_resolve, "triage": cmd_triage,
     "queue": cmd_queue, "pick": cmd_pick, "item-set": cmd_item_set,
     "needs-you": cmd_needs_you, "reconcile": cmd_reconcile, "priorities": cmd_priorities,
     }[args.cmd or "board"](args)


if __name__ == "__main__":
    main()
