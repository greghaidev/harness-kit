#!/usr/bin/env python3
"""hygiene.py — the retirement path for everything the rest of the kit writes.

THE GAP THIS CLOSES

Every other component in this kit CREATES. The store keeps notes, the journal keeps days, the
lanes keep items, and the Stop guard makes sure a session records its conclusion before it ends.
Nothing retires any of it.

That asymmetry is not a tidiness problem, it is a correctness problem, and it arrives on a
schedule. A store with a creating force and no closing force fills with items that read as open
and are not, and the first time you cannot tell those apart you stop trusting the list. A
follow-up list nobody trusts is worse than no list: it still costs you the reading.

The measured shape of the failure, from the harness this was extracted from: a follow-up pile
reached 231 open items while a weekly detection sweep ran green the entire time. Roughly half of
them had never been actions at all — they were status notes ("SHIPPED", "sprint summary") that
someone tagged follow-up out of habit, and a note with nothing to do can never be done, so it
sits there forever. Detection alone had found them repeatedly. Nothing closed them, because
closing needs judgment about evidence and the sweep had none.

So this component is two tiers, deliberately separated:

    hygiene sweep                  # DETECT — mechanical, model-free, decides nothing
    hygiene reconcile [--apply]    # DISPOSE — closes only what it can corroborate
    hygiene status                 # one digest line, cheap enough for every session start

THE RULE THE DISPOSITION TIER IS BUILT AROUND

**An inference must never masquerade as a verified fact.** This is the same rule the lane board
enforces for items, and it is here for the same reason, learned the same expensive way.

The obvious closing signal — *this follow-up mentions a PR, and that PR is merged* — is wrong
often enough to be dangerous. A note that cites a PR as the ORIGIN of a finding looks exactly
like a note whose work that PR shipped. Measured on a real store: of 15 notes offered as
"cites a merged PR", 15 were false. One cited a PR only to say that a neighboring surface was
already fine; that PR changed 54 files and not one of them was the file the note was about.
Auto-closing on the bare citation would have silently buried a live action — which is precisely
the failure the whole process exists to prevent.

So a candidate must clear three gates before it closes, and each gate exists because it caught
something real:

  1. **Token corroboration.** A distinctive token from the note's stated action must appear in
     what the PR actually CHANGED — its title or its changed paths. Not merely that it merged.
  2. **Contemporaneity.** A PR that merged within a day of the note being written cannot be that
     note's resolution. Three candidates that cleared gate 1 were written by the very session
     that shipped the PR, recording what it deliberately did NOT do.
  3. **The note's own words.** A body saying "deliberately did not touch this" or "left open" is
     the strongest possible statement that the cited PR is not closing evidence, sitting in plain
     text, and both survivors of gates 1 and 2 said exactly that.

Everything that fails a gate stays OPEN and is reported by name with the reason. Bias-to-escalate
beats bias-to-close: a missed close costs one line of noise, a wrong close costs a dropped
commitment you will never know you dropped.

WHAT THIS DELIBERATELY DOES NOT DO

It does not check lane items. `lanes reconcile` already does that against the same ground truth,
and a second implementation of one rule is a guarantee that the two will disagree and that
nobody will know which is authoritative.

It never rewrites a note. Closure here is a SUPERSESSION — a new note that points at what it
closes — so the trail survives and the store keeps its append-only property.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HARNESS = Path(os.path.expanduser(os.environ.get("HARNESS_HOME") or "~/harness"))
sys.path.insert(0, str(HARNESS / "core" / "01-memory"))

# The store import is deliberately NOT fatal at module load. `status` runs from a SessionStart
# hook on every single session, and a hook that dies noisily because a store path moved would
# make the whole component something you switch off. Only the commands that actually read the
# store insist on it — see require_store().
_STORE_ERROR = ""
try:
    import agentos_store as store
except Exception as e:                                          # noqa: BLE001
    store, _STORE_ERROR = None, str(e)


def require_store():
    if store is None:
        sys.exit(f"hygiene.py: cannot import the memory store ({_STORE_ERROR}).\n"
                 f"  Expected it at {HARNESS / 'core' / '01-memory' / 'agentos_store.py'}.\n"
                 f"  Set HARNESS_HOME if the kit is installed elsewhere.")
    return store

# ─────────────────────────────────────────────────────────────────────────────── configuration
#
# Read from the SAME lanes.json the board uses. A second config file for a second component that
# needs the same three facts (which repo, which trunk, which PR CLI) is how two answers to one
# question get into a system.

CONFIG_PATH = Path(os.path.expanduser(
    os.environ.get("HARNESS_LANES_CONFIG") or str(HARNESS / "lanes.json")))

DEFAULTS = {
    "tenant": None,          # None = every tenant the store serves
    "repo": None,
    "trunk": "origin/main",
    "pr_cli": "gh",
    "budget": {"open_followups": 150},
    "hygiene": {
        # A follow-up older than this is surfaced for a look. Not closed — looked at.
        "followup_stale_days": 21,
        # The sweep is expected to run weekly. Past this, `status` alarms: a detector that
        # silently stopped running looks identical to a clean store.
        "heartbeat_alarm_days": 8,
        # Phrases that mark a document as describing a way of working you have RETIRED. Yours
        # are specific to you, so this ships empty and says so rather than reading as clean.
        "retired_phrases": [],
        # Documents that encode how you work, and therefore rot when how you work changes.
        "agreement_docs": ["~/.claude/CLAUDE.md"],
    },
}


def _load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
        except Exception as e:                                  # noqa: BLE001
            sys.exit(f"hygiene.py: {CONFIG_PATH} is not readable JSON ({e}).")
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


CFG = _load_config()
HCFG = CFG["hygiene"]
TENANT = CFG.get("tenant") or None
REPO = Path(os.path.expanduser(CFG["repo"])) if CFG.get("repo") else None
PR_CLI = CFG.get("pr_cli") or None
STALE_DAYS = int(HCFG.get("followup_stale_days", 21))
ALARM_DAYS = int(HCFG.get("heartbeat_alarm_days", 8))
RETIRED_PHRASES = [p for p in (HCFG.get("retired_phrases") or []) if p.strip()]
AGREEMENT_DOCS = [Path(os.path.expanduser(p)) for p in (HCFG.get("agreement_docs") or [])]
BUDGET_FOLLOWUPS = int(CFG["budget"].get("open_followups", 150))

STATE = Path(os.path.expanduser(
    os.environ.get("HARNESS_HYGIENE_STATE") or str(HARNESS / "state" / "hygiene")))
STATUS_JSON = STATE / "status.json"
HEARTBEAT_ID = "hygiene-sweep-heartbeat"
HEARTBEAT_TENANT = "meta"          # how the harness runs is meta, not work

# ─────────────────────────────────────────────────────────────────────────────── classifiers

# A title that ANNOUNCES something finished. Matched against titles only — see ACTION_MARKERS.
TERMINAL_TOKENS = re.compile(
    r"\b(SHIPPED|MERGED|LIVE|CLOSED|COMPLETE|COMPLETED|DONE|FIXED)\b"
    r"|\bsummary\b|\bretro\b|\bwrite-?up\b", re.I)

PR_REF = re.compile(r"(?:pull/|PR\s*#|(?<![\w/])#)(\d{1,6})\b", re.I)

# A follow-up whose BODY declares an action is NOT a mis-tag, however terminal its title reads.
#
# This exists because the title check alone is not precise enough to act on. Run against a real
# store it flagged 10 of 18 genuine single-action follow-ups whose titles merely CONTAINED a
# trigger word — including live work whose description mentioned a "SHIPPED" view. A title tells
# you what a note is ABOUT; only the body tells you whether anything is left to do. Requiring
# both keeps the class small enough that closing it needs no judgment.
ACTION_MARKERS = re.compile(
    r"\bONE[- ]ACTION\b|\bONE unfinished action\b|\bNEXT STEP\b|\bTODO\b"
    r"|\bstill (?:need|open|to)\b|\bnot (?:yet |been )?(?:done|built|shipped|applied|wired|run)\b"
    r"|\bpending\b|\bawait|\bmust (?:be |still )?\w+|\bblocked[- ]on\b"
    r"|\bremains? (?:open|to)\b|\bcarries to\b", re.I)


def is_mistag(title: str, body: str) -> bool:
    """True when a follow-up is really a status note with nothing left to close.

    Requires BOTH a terminal-reading title AND no action declared anywhere in the body.
    """
    if not TERMINAL_TOKENS.search(title or ""):
        return False
    return not ACTION_MARKERS.search(body or "")


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_days(dt):
    return (_now() - dt).days if dt else None


def _split_frontmatter(raw: str):
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    try:
        import yaml
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:                                           # noqa: BLE001
        fm = {}
    return (fm if isinstance(fm, dict) else {}), parts[2]


def _note_source(row):
    """(frontmatter, body) read from the note file the search row points at."""
    p = row.get("path")
    if not p:
        return {}, ""
    try:
        return _split_frontmatter(Path(p).read_text(errors="replace"))
    except OSError:
        return {}, ""


# ─────────────────────────────────────────────────────────────────────────── ground truth (PRs)

_PR_INDEX_CACHE = {}
_PR_FACTS_CACHE = {}


def _why_no_pr():
    """Named precisely, because 'unavailable' covers three situations with three different fixes."""
    if REPO is None:
        return f"no repo configured in {CONFIG_PATH.name}"
    if not (REPO / ".git").exists():
        return f"{REPO} is not a git repository"
    if not PR_CLI:
        return f"no PR CLI configured (`pr_cli` is null in {CONFIG_PATH.name})"
    return f"`{PR_CLI}` failed or is not installed"


def pr_index():
    """{number: state} for every PR, or None when the question could not be asked.

    None is load-bearing. A caller that treats it as an empty dict reports "nothing merged",
    which is indistinguishable from a clean store and is the way a gate becomes decoration.
    """
    if "v" in _PR_INDEX_CACHE:
        return _PR_INDEX_CACHE["v"]
    idx = None
    if PR_CLI and REPO is not None and (REPO / ".git").exists():
        try:
            raw = subprocess.run(
                [PR_CLI, "pr", "list", "--state", "all", "--limit", "1000",
                 "--json", "number,state"],
                cwd=REPO, capture_output=True, text=True, timeout=60).stdout
            idx = {p["number"]: p["state"] for p in json.loads(raw)}
        except Exception:                                       # noqa: BLE001
            idx = None
    _PR_INDEX_CACHE["v"] = idx
    return idx


def pr_facts(num):
    """(haystack, merged_at) — the PR's title plus its changed paths, lowercased, and when it
    merged. (None, None) when the lookup failed, so a failure can never read as evidence."""
    if num in _PR_FACTS_CACHE:
        return _PR_FACTS_CACHE[num]
    hay, merged = None, None
    if PR_CLI and REPO is not None:
        try:
            raw = subprocess.run(
                [PR_CLI, "pr", "view", str(num), "--json", "title,files,mergedAt"],
                cwd=REPO, capture_output=True, text=True, timeout=60).stdout
            d = json.loads(raw)
            hay = ((d.get("title") or "") + " "
                   + " ".join(f.get("path", "") for f in (d.get("files") or []))).lower()
            merged = _parse_ts(d.get("mergedAt"))
        except Exception:                                       # noqa: BLE001
            hay, merged = None, None
    _PR_FACTS_CACHE[num] = (hay, merged)
    return hay, merged


# ─────────────────────────────────────────────────────────────────────────────── the sweep

def scan_followups():
    """Classify every open follow-up. Mechanical: ground truth decides, never a recalled claim."""
    require_store()
    out = {"open": 0, "partial": 0, "mistag": [], "pr_candidates": [], "abandoned": [],
           "stale": [], "pr_checked": True}
    prs = pr_index()
    if prs is None:
        out["pr_checked"] = False
        prs = {}

    for row in store.followups(tenant=TENANT):
        if row.get("state") == "partial":
            out["partial"] += 1
            continue
        out["open"] += 1
        fm, body = _note_source(row)
        title = row.get("title") or ""
        refs = {int(n) for n in PR_REF.findall(f"{title} {body}")}
        merged = sorted(n for n in refs if prs.get(n) == "MERGED")
        abandoned = sorted(n for n in refs if prs.get(n) == "CLOSED")
        age = _age_days(_parse_ts(row.get("updated"))) or 0
        rec = {"id": row["id"], "tenant": row.get("tenant"), "title": title, "body": body,
               "created": _parse_ts(fm.get("created")), "age": age}

        if is_mistag(title, body):
            out["mistag"].append(rec)
        elif merged:
            out["pr_candidates"].append({**rec, "prs": merged, "abandoned": abandoned})
        elif abandoned:
            # Surfaced, never closed. A CLOSED-unmerged PR means the work was ABANDONED, so the
            # follow-up is probably still LIVE and may need re-cutting rather than closing.
            out["abandoned"].append({**rec, "prs": abandoned})
        elif age > STALE_DAYS:
            out["stale"].append(rec)
    return out


def scan_capabilities():
    """Capabilities whose title announces completion but which close nothing.

    A 'this is done' registration is the natural home for the follow-ups it absorbs, and the
    store's own contract says so. One that supersedes nothing has left that work open somewhere
    else, permanently, while reading as finished here.
    """
    require_store()
    bad = []
    for row in store.search(tenant=TENANT, type="capability", limit=1000):
        if not TERMINAL_TOKENS.search(row.get("title") or ""):
            continue
        rels = {l.get("relation") for l in (row.get("links") or []) if isinstance(l, dict)}
        if not (rels & store.SUPERSEDE_RELATIONS):
            bad.append(f"{row['id']} — '{(row.get('title') or '')[:70]}'")
    return sorted(bad)


# THE RETARGET.
#
# Everything above this line is about work. The residue risk in this kit is not a dropped task,
# it is a CONCLUSION that outlives the evidence under it — a number that left a session with a
# source, and is still being quoted after that source moved, changed or stopped existing.
#
# Two checks, both cheap, both mechanical. Neither can tell you a conclusion is wrong; they tell
# you it can no longer be DEFENDED, which is a different and more answerable question.

_PATHLIKE = re.compile(r"^[~./]?[\w./-]+\.\w{1,8}$")


def scan_conclusions():
    require_store()
    out = {"total": 0, "unsourced": [], "broken_source": []}
    for row in store.search(tenant=TENANT, tags=["conclusion"], limit=1000):
        _fm, body = _note_source(row)
        out["total"] += 1
        m = re.search(r"^\s*source\s*:\s*(.*)$", body, re.M | re.I)
        src = (m.group(1).strip() if m else "")
        title = (row.get("title") or "")[:80]
        if not src or src.upper() == "UNSOURCED":
            out["unsourced"].append(f"{row['id']} — '{title}'")
            continue
        # Only a source that NAMES A FILE can be checked mechanically. "the Q3 close" is a real
        # source and is not checkable here; saying nothing about it beats guessing.
        if not _PATHLIKE.match(src):
            continue
        cand = Path(os.path.expanduser(src))
        roots = [cand] + ([REPO / src] if REPO is not None and not cand.is_absolute() else [])
        if not any(r.exists() for r in roots):
            out["broken_source"].append(f"{row['id']} — source `{src}` no longer exists")
    return out


def scan_agreement_docs():
    """Documents that describe a way of working you have retired.

    Reports its own inertness. A configured-empty check that prints nothing is indistinguishable
    from a clean result, and the whole point of this file is to not do that.
    """
    if not RETIRED_PHRASES:
        return {"configured": False, "hits": []}
    hits = []
    for doc in AGREEMENT_DOCS:
        if not doc.exists():
            continue
        for n, line in enumerate(doc.read_text(errors="replace").splitlines(), 1):
            for phrase in RETIRED_PHRASES:
                if phrase.lower() in line.lower():
                    # A line that already says the thing is retired is documentation, not rot.
                    if re.search(r"retired|superseded|obsolete|replaced", line, re.I):
                        continue
                    hits.append(f"{doc}:{n}: '{phrase}'")
                    break
    return {"configured": True, "hits": sorted(set(hits))}


def cmd_sweep(no_heartbeat=False):
    f = scan_followups()
    caps = scan_capabilities()
    concl = scan_conclusions()
    docs = scan_agreement_docs()

    counts = {
        "open_followups": f["open"], "partial": f["partial"],
        "mistag": len(f["mistag"]), "pr_candidates": len(f["pr_candidates"]),
        "abandoned_pr": len(f["abandoned"]), "stale": len(f["stale"]),
        "terminal_no_supersedes": len(caps),
        "conclusions": concl["total"], "unsourced_conclusions": len(concl["unsourced"]),
        "broken_source_conclusions": len(concl["broken_source"]),
        "retired_phrase_hits": len(docs["hits"]),
        "retired_phrases_configured": docs["configured"],
        "pr_ground_truth": f["pr_checked"],
        "over_budget": f["open"] > BUDGET_FOLLOWUPS,
    }

    STATE.mkdir(parents=True, exist_ok=True)
    report = STATE / f"report-{_now().date()}.md"
    lines = [f"# Memory hygiene — {_now().isoformat(timespec='seconds')}", "",
             f"Counts: `{json.dumps(counts)}`", ""]
    if not f["pr_checked"]:
        lines += [f"> **PR ground truth SKIPPED** — {_why_no_pr()}. Every class below that rests "
                  f"on 'did this ship' is missing from this run. This is not a clean result.", ""]
    sections = [
        ("Mis-tagged follow-ups (terminal title, no action in the body) — closable without "
         "judgment by `reconcile`", [f"{c['id']} — '{c['title'][:70]}'" for c in f["mistag"]]),
        ("Cites a MERGED PR — a CANDIDATE ONLY. Never close on sight; `reconcile` corroborates "
         "each one against what the PR actually changed",
         [f"{c['id']} — PR(s) {c['prs']} merged" for c in f["pr_candidates"]]),
        ("Cites a PR that was CLOSED UNMERGED — the work never shipped, so this is probably "
         "still LIVE. Re-cut it or drop it; never auto-close it",
         [f"{c['id']} — PR(s) {c['prs']}" for c in f["abandoned"]]),
        (f"Stale follow-ups (>{STALE_DAYS}d) — verify against ground truth, then supersede or "
         f"refresh", [f"{c['id']} ({c['age']}d)" for c in f["stale"]]),
        ("Capabilities titled as complete that supersede nothing", caps),
        ("Conclusions on the record with NO source — these cannot be defended when quoted back",
         concl["unsourced"]),
        ("Conclusions whose source no longer exists — the evidence moved out from under them",
         concl["broken_source"]),
        ("Retired-phrase hits in your working agreements" if docs["configured"] else
         "Retired-phrase scan NOT CONFIGURED — `hygiene.retired_phrases` is empty, so this check "
         "found nothing because it looked for nothing", docs["hits"]),
    ]
    for title, items in sections:
        lines += [f"## {title} ({len(items)})", ""]
        lines += [f"- {i}" for i in items] or ["- none"]
        lines.append("")
    report.write_text("\n".join(lines))

    counts["last_run"] = _now().isoformat(timespec="seconds")
    counts["report"] = str(report)
    STATUS_JSON.write_text(json.dumps(counts, indent=1))

    if not no_heartbeat:
        _write_heartbeat(counts)
    if not f["pr_checked"]:
        print(f"WARN: PR ground truth SKIPPED — {_why_no_pr()}")
    print(f"sweep done -> {report}")
    print(f"  {json.dumps(counts)}")
    return 0


def _write_heartbeat(counts):
    """Record that the sweep ran, in the store, where a later session can see it.

    The status file alone cannot prove this: a status file is local, and a sweep that has been
    dead for a month leaves a status file that looks exactly like a sweep that ran an hour ago
    until you read the timestamp. The heartbeat note puts the run itself in the record.
    """
    try:
        store.put({
            "id": HEARTBEAT_ID, "tenant": HEARTBEAT_TENANT, "type": "semantic",
            "title": f"Memory-hygiene sweep — {_now().date()}",
            "tags": ["hygiene", "heartbeat"],
            "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
            "body": "Written by `hygiene sweep`. Counts at the last run:\n\n"
                    f"```json\n{json.dumps(counts, indent=1)}\n```",
        })
        return True
    except Exception as e:                                      # noqa: BLE001
        print(f"WARN heartbeat write failed: {e}", file=sys.stderr)
        return False


def cmd_status():
    """One to three lines, cheap enough to run at every session start. Never asks the network."""
    if not STATUS_JSON.exists():
        print("Memory hygiene: NEVER RUN — run `hygiene sweep`")
        return 0
    try:
        c = json.loads(STATUS_JSON.read_text())
    except Exception:                                           # noqa: BLE001
        print("Memory hygiene: status file unreadable — run `hygiene sweep`")
        return 0
    age = _age_days(_parse_ts(c.get("last_run")))
    if age is not None and age > ALARM_DAYS:
        print(f"MEMORY HYGIENE STALE — last sweep {age}d ago. A detector that stopped running "
              f"looks exactly like a clean store. Run: hygiene sweep")
    print(f"Memory hygiene: last sweep {age}d ago — {c.get('open_followups')} open follow-ups "
          f"({c.get('mistag')} closable now, {c.get('pr_candidates')} cite a merged PR, "
          f"{c.get('stale')} stale), {c.get('unsourced_conclusions')} unsourced conclusion(s). "
          f"Report: {c.get('report')}")
    if c.get("over_budget"):
        print(f"  → over the {BUDGET_FOLLOWUPS} follow-up budget. Retire something before "
              f"creating more.")
    if c.get("mistag"):
        print(f"  → {c['mistag']} closable without judgment: hygiene reconcile --apply")
    if not c.get("pr_ground_truth", True):
        print(f"  → the last sweep could NOT check what shipped ({_why_no_pr()}) — its "
              f"ship-dependent classes are missing, not empty.")
    return 0


# ─────────────────────────────────────────────────────────────────────────────── the disposition

# Words too common to corroborate anything. A plain token that hits half your repository is not
# evidence that a PR did the thing a note asked for.
_GENERIC = set("""
the a an and or of for to in on at is are was were be been it its this that with without from
into over under after before again still not no yes one two all any some each per via using use
used add adds added fix fixes fixed make makes made ship ships shipped run runs ran new old real
follow up action item note notes todo work works needs need should must page pages file files
test tests testing code data value values check checks checked update updates updated build
builds built change changes changed docs doc script scripts main prod staging live local repo
branch commit merge merged pull request now then when where what why how which who has have had
does did done open close closed stale line lines row rows table report reports
""".split())

PLAIN_TOKEN_QUORUM = 2
CONTEMPORANEOUS_HOURS = 24

# A note that talks about its PR in the NEGATIVE is citing the thing that LEFT this work undone.
_ORIGIN_PHRASE = re.compile(
    r"(?:did\s+not|didn't|does\s+not|deliberately\s+not|not\s+done\s+in|not\s+fixed|"
    r"not\s+touch|left\s+open|still\s+(?:open|stands|remains|live)|the\s+only\s+one\s+not|"
    r"except|deferred|out\s+of\s+scope)", re.I)


def action_tokens(title: str, body: str):
    """(strong, plain) tokens describing the note's stated action.

    STRONG tokens are identifier-shaped — they carry a dot, slash or underscore
    (`dim_customer.sql`, `etl/staging`). A strong token appearing in a PR's changed paths is
    near-proof that the PR touched the thing the note is about. PLAIN tokens are ordinary
    English, which match paths far too easily, so they corroborate only in numbers — and never
    when strong tokens exist and every one of them missed, because a note that names a file and
    a PR that did not touch that file are not about the same work.
    """
    text = (title or "") + " "
    m = re.search(r"^\s*(?:ONE ACTION|ACTION)\s*:?\s*(.+)$", body or "", re.M | re.I)
    text += m.group(1) if m else " ".join((body or "").strip().splitlines()[:2])
    strong, plain = set(), set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9._/-]{2,}", text.lower()):
        t = raw.strip("._/-")
        if len(t) < 4 or t in _GENERIC:
            continue
        (strong if re.search(r"[._/]", t) else plain).add(t)
    return strong, plain


def corroborate(cand):
    """(hit, why_not) — hit is (pr, matched_tokens) only when all three gates pass."""
    strong, plain = action_tokens(cand["title"], cand["body"])
    why_not = ""
    for pr in cand["prs"]:
        hay, merged = pr_facts(pr)
        if hay is None:
            why_not = f"#{pr} could not be read — {_why_no_pr()}"
            continue
        if merged and cand.get("created"):
            delta_h = (merged - cand["created"]).total_seconds() / 3600
            if delta_h < CONTEMPORANEOUS_HOURS:
                why_not = (
                    f"#{pr} merged {abs(round(delta_h, 1))}h "
                    + ("after the note was written — contemporaneous, so the PR is the finding's "
                       "origin, not its resolution" if delta_h >= 0 else
                       "BEFORE the note was written — the note postdates the merge, so that PR "
                       "cannot have shipped it"))
                continue
        origin = _ORIGIN_PHRASE.search(cand["body"] or "")
        if origin:
            why_not = (f"the note itself says the cited PR left this undone "
                       f"(matched '{origin.group(0)}')")
            continue
        s_hit = sorted(t for t in strong if t in hay)
        if s_hit:
            return (pr, s_hit[:4]), ""
        if strong:
            # It named files; the PR touched none of them. That is a positive signal AGAINST.
            why_not = (f"#{pr} changed nothing matching {sorted(strong)[:5]}")
            continue
        p_hit = sorted(t for t in plain if t in hay)
        if len(p_hit) >= PLAIN_TOKEN_QUORUM:
            return (pr, p_hit[:4]), ""
        why_not = f"#{pr} matched fewer than {PLAIN_TOKEN_QUORUM} distinctive words of the action"
    return None, why_not or "no cited PR could be corroborated"


def cmd_reconcile(apply=False):
    f = scan_followups()
    if not f["pr_checked"]:
        print(f"ABORT: cannot corroborate anything — {_why_no_pr()}.\n"
              f"       Nothing closed. This is a SKIPPED run, not a clean one.", file=sys.stderr)
        return 2

    confirmed, unverified = [], []
    for cand in f["pr_candidates"]:
        hit, why = corroborate(cand)
        (confirmed if hit else unverified).append((cand, hit, why))

    print(f"reconcile: {len(f['pr_candidates'])} PR-referenced candidate(s) -> "
          f"{len(confirmed)} corroborated, {len(unverified)} UNVERIFIED (left open)")
    print(f"           {len(f['mistag'])} mis-tagged (terminal title, no action) -> reclassify\n")

    lines = []
    for cand, hit, _ in confirmed:
        pr, overlap = hit
        print(f"  CLOSE      {cand['id']}\n             PR #{pr} changed {overlap}")
        lines.append(f"- `{cand['id']}` — shipped by PR #{pr} "
                     f"(changed paths match: {', '.join(overlap)})")
    for cand, _, why in unverified:
        print(f"  UNVERIFIED {cand['id']}\n             cites merged {cand['prs']} but {why} "
              f"— LEFT OPEN")
    for cand in f["mistag"]:
        print(f"  RECLASS    {cand['id']} — '{cand['title'][:60]}'")
        lines.append(f"- `{cand['id']}` — reclassified: terminal title, carries no action to close")

    closable = [c["id"] for c, _, _ in confirmed] + [c["id"] for c in f["mistag"]]
    if not apply:
        print(f"\ndry run — {len(closable)} note(s) would be superseded. Re-run with --apply.")
        return 0
    if not closable:
        print("\nnothing to close.")
        return 0

    body = (
        "Written by `hygiene reconcile --apply`. Every id below was corroborated against what "
        "its referenced PR actually CHANGED — title and changed paths — not merely against that "
        "PR being merged.\n\n" + "\n".join(lines)
        + f"\n\nLeft OPEN as unverified ({len(unverified)}): each cites a merged PR that shipped "
          "nothing matching its stated action.\n"
        + "\n".join(f"- `{c['id']}` — {why}" for c, _, why in unverified))
    rec = store.put({
        "id": f"{_now().date()}-hygiene-reconcile",
        "tenant": (TENANT or "work"), "type": "episodic",
        "title": f"Hygiene reconcile {_now().date()} — {len(closable)} follow-up(s) closed",
        "tags": ["hygiene", "reconciliation"],
        "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
        "body": body, "supersedes": closable,
    })
    print(f"\nAPPLIED — superseded {len(closable)} note(s) via {rec.get('id')}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Memory hygiene — detect what has gone stale, close only what corroborates.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="one digest line; cheap, no network")
    p_sw = sub.add_parser("sweep", help="full mechanical scan + report")
    p_sw.add_argument("--no-heartbeat", action="store_true",
                      help="do not write the heartbeat note (used by tests)")
    p_rc = sub.add_parser("reconcile", help="close what can be corroborated")
    p_rc.add_argument("--apply", action="store_true",
                      help="write the supersession note (default: dry run)")
    sub.add_parser("config", help="print the configuration in force")
    a = ap.parse_args()

    if a.cmd == "status":
        return cmd_status()
    if a.cmd == "sweep":
        return cmd_sweep(no_heartbeat=a.no_heartbeat)
    if a.cmd == "reconcile":
        return cmd_reconcile(apply=a.apply)
    if a.cmd == "config":
        print(f"config file : {CONFIG_PATH}{'' if CONFIG_PATH.exists() else ' (absent — defaults)'}")
        print(f"tenant      : {TENANT or 'all'}")
        print(f"repo        : {REPO or 'NOT CONFIGURED — ship checks will report SKIPPED'}")
        print(f"pr cli      : {PR_CLI or 'none'}")
        print(f"state       : {STATE}")
        print(f"stale after : {STALE_DAYS}d    alarm after: {ALARM_DAYS}d")
        print(f"retired     : {len(RETIRED_PHRASES)} phrase(s)"
              + ("" if RETIRED_PHRASES else " — that check is INERT until you configure it"))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
