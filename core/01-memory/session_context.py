#!/usr/bin/env python3
"""SessionStart hook — print a compact Agent OS context digest so every Claude Code session
starts infra-aware: open follow-ups (newest AND oldest) plus registered capabilities. Fast,
read-only, never blocks or crashes the session — but a real problem (durability alarm, store
outage) is now surfaced as a line rather than silently dropped; the digest fails toward a visible
line, never toward total silence (see the AOS.1-ops audit's "alarm layer fails toward silence").

Follow-up state is derived passively from links (see agentos_store.followups): a follow-up drops
off this list once a newer note — usually the auto-updated capability — supersedes it, and shows
as "partially done" when something only advances it. Nobody has to mark anything `done` by hand.

Open follow-ups are shown as the newest few UNION the oldest few (not just the newest cap). At the
current create rate (~15/day) a plain "N newest" window churns over in under a day, so the oldest
debt never resurfaces at session start — exactly when the protocol says to reconcile it — and the
"(stale Nd)" flag on those aged items could mathematically never render. Splitting the budget keeps
same-day continuity (newest) while finally surfacing the aging debt (oldest, where the stale flag
now actually shows). The digest layout is pure (render_digest / select_followups / dedupe_caps take
data and return values), so it is testable without the store; main() does the store I/O + printing."""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STALE_DAYS = 14
# Open-follow-up display budget: at most FOLLOWUP_CAP lines, split into the newest FOLLOWUP_HALF
# (continuity) and the oldest FOLLOWUP_HALF (aging debt), deduped. Under the cap → show all.
FOLLOWUP_CAP, FOLLOWUP_HALF, CAP_CAP = 14, 7, 16

# --- durability alarm ------------------------------------------------------------------------
# deploy/archive_sync.sh writes <state>/durability.json every ~15 min (see that script's header
# for the shared contract); deploy/health_sweep.py reads the same file. Checking it here is cheap
# (one stat + a small JSON/JSONL read, never a store scan) and evaluated in main() BEFORE and
# INDEPENDENTLY of the store-backed digest (capabilities/follow-ups), so neither a malformed
# durability.json nor a store outage can suppress the other's signal (see the AOS.1-ops audit,
# findings F1/F2/F3/F5/F6 — durability alarms used to silently disappear on exactly these inputs).
DURABILITY_STALE_SECS = 2 * 60 * 60        # alarm if a store's last successful push is older
COMMIT_FAILURE_WINDOW_SECS = 24 * 60 * 60  # alarm if commit-failures.jsonl has entries this fresh
CLOCK_SKEW_TOLERANCE_SECS = 300            # a last_success this far in the future is skew, not noise
# archive_sync.sh writes <state>/archive-sync.heartbeat BEFORE any push attempt and removes it only
# once the run completes (success or failure). A heartbeat older than this means a run started and
# never finished — durability.json alone can't show that (a crash before its end-of-script writer
# leaves durability.json untouched, looking exactly as fresh as the LAST successful run). Set well
# above the sync's normal runtime (seconds) but well below DURABILITY_STALE_SECS, so a stuck run is
# caught fast instead of waiting out the full staleness window.
HEARTBEAT_STUCK_SECS = 20 * 60


def _age_days(ts):
    try:
        dt = datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).days
    except Exception:
        return None


def select_followups(open_f, cap=FOLLOWUP_CAP, half=FOLLOWUP_HALF):
    """Choose which open follow-ups the digest shows, given `open_f` newest-first.

    Showing only the newest `cap` means — at ~15 new follow-ups/day — the oldest debt never
    resurfaces and its stale flag never renders (dead code). So we split the budget: the newest
    `half` (same-day continuity) UNION the oldest `half` (the aging debt), deduped by id, oldest
    appended after newest. When the total is within the cap, show everything (no duplication).

    Returns (shown_list, hidden_count).
    """
    if len(open_f) <= cap:
        return list(open_f), 0
    newest = open_f[:half]
    seen = {n["id"] for n in newest}
    oldest = [n for n in open_f[-half:] if n["id"] not in seen]
    shown = newest + oldest
    return shown, len(open_f) - len(shown)


def dedupe_caps(caps):
    """Dedupe capabilities by id, preserving first-seen order.

    A capability with a stable id registered in two tenants (e.g. cap-agent-os-dashboard in both
    `work` and `meta`) otherwise prints once per tenant. It is one capability — show it once.
    """
    seen, out = set(), []
    for c in caps:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        out.append(c)
    return out


def _age_seconds(ts):
    try:
        dt = datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def durability_alarm_reasons(doc, recent_failures=0, heartbeat_age=None):
    """Pure: given a parsed durability.json (`doc`, or None if missing/unreadable), a count of
    commit-failures.jsonl entries newer than COMMIT_FAILURE_WINDOW_SECS, and the age in seconds of
    archive_sync.sh's heartbeat file (`heartbeat_age`, or None if the heartbeat is absent — the
    normal "no run currently in flight" state), return the alarm reasons (empty list = nothing
    wrong). No I/O here — see `_load_durability_state` for the file reads.

    heartbeat_age catches a crash BEFORE archive_sync.sh's end-of-script durability.json writer
    (AOS.1-ops audit, finding F4): that writer only runs once, at the very end, so a run that dies
    partway through never touches durability.json at all — the digest would otherwise see the
    PREVIOUS run's fresh, all-ok durability.json and stay completely silent for up to
    DURABILITY_STALE_SECS about the fact a newer run started and never finished.

    Shape-guarded end to end: durability.json is an external, hand-editable/crash-truncatable
    file, so `doc` may be valid JSON of the WRONG SHAPE (a list/number/string at the top level,
    a non-dict `stores`, or a non-dict per-store entry). Every one of those must produce an alarm
    reason, never an AttributeError/TypeError — a malformed file is exactly the kind of thing this
    alarm exists to catch, and letting an exception escape here means a caller-side try/except
    silently swallows the whole alarm (see the AOS.1-ops audit, finding F1 — this was the bug)."""
    reasons = []
    if doc is None:
        reasons.append("durability.json missing or unreadable")
    elif not isinstance(doc, dict):
        reasons.append(f"durability.json malformed: expected an object, got {type(doc).__name__}")
    else:
        stores = doc.get("stores")
        if not isinstance(stores, dict):
            if stores is None:
                reasons.append("durability.json has no store entries (stores missing/null)")
            else:
                reasons.append(
                    f"durability.json malformed: 'stores' must be an object, got {type(stores).__name__}")
        elif not stores:
            reasons.append("durability.json has no store entries (stores empty)")
        else:
            for name, info in sorted(stores.items()):
                if not isinstance(info, dict):
                    reasons.append(
                        f"{name}: malformed store entry (expected an object, got {type(info).__name__})")
                    continue
                # `is True`, not truthiness: ok:"false" (a string) or ok:0 must alarm, not read as success.
                if info.get("ok") is not True:
                    reasons.append(f"{name} push failed ({info.get('msg') or 'no detail'})")
                    continue
                age = _age_seconds(info.get("last_success"))
                if age is None:
                    reasons.append(f"{name} last_success missing/unparseable")
                elif age < -CLOCK_SKEW_TOLERANCE_SECS:
                    reasons.append(f"{name} last_success is in the future by {int(-age)}s (clock skew?)")
                elif age > DURABILITY_STALE_SECS:
                    reasons.append(f"{name} last pushed {int(age // 3600)}h ago")
    if recent_failures:
        reasons.append(f"{recent_failures} commit-failure(s) in the last 24h")
    if heartbeat_age is not None and heartbeat_age > HEARTBEAT_STUCK_SECS:
        reasons.append(
            f"archive-sync heartbeat started {int(heartbeat_age // 60)}min ago and never completed "
            "(stuck/crashed run?)")
    return reasons


def render_alarm(reasons):
    """Pure: format alarm reasons into the single line prepended to the digest, or None."""
    if not reasons:
        return None
    return "⚠ DURABILITY: " + "; ".join(reasons)


def _load_durability_state(state_dir):
    """I/O only: stat + small JSON/JSONL/text reads, no store scan, never raises.

    Any problem reading durability.json degrades to doc=None (which durability_alarm_reasons
    reports as "missing or unreadable" — the correct alarm either way); any problem reading
    commit-failures.jsonl degrades to 0 recent failures (fails toward silence, per the "never
    crash the digest" contract — a broken state dir must never became a worse outage than the
    one it's trying to surface). Any problem reading archive-sync.heartbeat (most commonly: it
    doesn't exist, because no sync has run since the last clean completion — the normal case)
    degrades to heartbeat_age=None, meaning "no run currently in flight" rather than an alarm.

    Returns (doc, recent_commit_failures, heartbeat_age_seconds).
    """
    state_dir = Path(state_dir)
    doc = None
    try:
        doc = json.loads((state_dir / "durability.json").read_text(encoding="utf-8"))
    except Exception:
        doc = None

    recent = 0
    try:
        with open(state_dir / "commit-failures.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                age = _age_seconds(row.get("ts"))
                if age is not None and age <= COMMIT_FAILURE_WINDOW_SECS:
                    recent += 1
    except Exception:
        pass

    heartbeat_age = None
    try:
        raw = (state_dir / "archive-sync.heartbeat").read_text(encoding="utf-8").strip()
        heartbeat_age = _age_seconds(raw) if raw else None
    except Exception:
        heartbeat_age = None

    return doc, recent, heartbeat_age


def render_digest(caps, open_f, partial_f):
    """Build the digest text from already-fetched data. Pure (no store I/O), so it is unit-testable."""
    out = ["## Agent OS memory — recent context (query the `agent-os` MCP server for more)"]

    if open_f:
        shown, hidden = select_followups(open_f)
        out.append("\n**Open follow-ups:** _(newest + oldest)_")
        for n in shown:
            age = _age_days(n.get("updated"))
            stale = f" _(stale {age}d — still real?)_" if age is not None and age >= STALE_DAYS else ""
            out.append(f"- [{n['tenant']}] {n['title']} — `{n['id']}`{stale}")
        if hidden:
            out.append(f"- _…and {hidden} more — `memory_search(tags=['follow-up'])`_")

    if partial_f:
        out.append("\n**Partially done (in progress):**")
        for n in partial_f[:8]:
            by = ", ".join(n.get("advanced_by") or []) or "?"
            out.append(f"- [{n['tenant']}] {n['title']} — `{n['id']}` ↳ advanced by `{by}`")
        if len(partial_f) > 8:
            out.append(f"- _…and {len(partial_f) - 8} more — `memory_search(tags=['follow-up'])`_")

    if caps:
        caps = dedupe_caps(caps)
        out.append("\n**Registered capabilities:**")
        for c in caps[:CAP_CAP]:
            status = (c.get("health") or {}).get("status", "")
            kind = c.get("capability_type") or "?"
            out.append(f"- {c['title']} ({kind}{', ' + status if status else ''}) — `{c['id']}`")
        if len(caps) > CAP_CAP:
            out.append(f"- _…and {len(caps) - CAP_CAP} more — `registry_find()`_")

    out.append("\n_Journaling is on: capture friction / decisions / forgotten things / ideas / "
               "follow-ups / capabilities as you work. Close follow-ups passively — update the "
               "capability and point it `supersedes`/`advances` at the follow-up; never hand-mark "
               "`done` (see CLAUDE.md)._")
    return "\n".join(out)


def _render_gov_waiting():
    """`lane.py gov --waiting` (Governance Visibility Board, PR #477) against the
    checkout: exit 1 + stdout means something is parked on the operator, exit 0 means nothing is.
    Wired here per meta follow-up ...802c10 — this file lives outside the AI repo, so the
    board's "waiting on you" section previously only appeared when the operator ran `gov` by hand."""
    try:
        gov = subprocess.run(
            [sys.executable, os.path.join(
                os.environ.get("HARNESS_HOME", os.path.expanduser("~/harness")),
                "menu", "lanes", "lane.py"), "gov", "--waiting"],
            capture_output=True, text=True, timeout=10,
        )
        if gov.returncode == 1 and gov.stdout.strip():
            return gov.stdout.strip()
    except Exception:
        pass
    return None


def main():
    # The durability alarm is evaluated FIRST and INDEPENDENTLY of the store-backed digest below.
    # Previously this ran after `import agentos_store; s.find(); s.followups()`, so a store outage
    # raised there and hit sys.exit(0) BEFORE the alarm was ever computed — silencing the one alarm
    # that most needs to fire when the store is down (AOS.1-ops audit, finding F2). Ordering this
    # block first — and giving its own except-branch a non-None fallback line instead of swallowing
    # to None — means a durability problem can now survive a simultaneous store outage.
    try:
        state_dir = os.environ.get("AGENTOS_STATE") or os.path.expanduser("~/.local/state/agent-os")
        doc, recent_failures, heartbeat_age = _load_durability_state(state_dir)
        alarm_line = render_alarm(durability_alarm_reasons(doc, recent_failures, heartbeat_age))
    except Exception as e:
        # durability_alarm_reasons/_load_durability_state are written to never raise, so reaching
        # this is itself unexpected — but "unexpected" must still surface, not go dark next to a
        # potentially-dead store (never let the digest's own alarm code be the silent failure).
        alarm_line = f"⚠ DURABILITY: alarm check crashed unexpectedly ({type(e).__name__}: {e})"

    # Store-backed digest (capabilities + follow-ups): independent of the alarm above. A store
    # outage here must not suppress the durability alarm (see above) and must not go completely
    # silent either — it now surfaces its own line instead of a bare sys.exit(0).
    caps, open_f, partial_f, store_error = [], [], [], None
    try:
        import agentos_store as s
        caps = s.find()
        fups = s.followups()  # open + partial, classified from links/tags; closed are excluded
        open_f = [f for f in fups if f.get("state") == "open"]
        partial_f = [f for f in fups if f.get("state") == "partial"]
    except Exception as e:
        store_error = f"⚠ AGENT-OS STORE: digest unavailable ({type(e).__name__}: {e})"

    # Independent of both blocks above, same reasoning: must not be silenced by an empty digest
    # and must not itself suppress the alarm/store-error/digest if it crashes or finds nothing.
    gov_line = _render_gov_waiting()

    if not (caps or open_f or partial_f or alarm_line or store_error or gov_line):
        sys.exit(0)

    parts = [alarm_line] if alarm_line else []
    if store_error:
        parts.append(store_error)
    if gov_line:
        parts.append(gov_line)
    if caps or open_f or partial_f:
        parts.append(render_digest(caps, open_f, partial_f))
    print("\n\n".join(parts))


if __name__ == "__main__":
    main()
