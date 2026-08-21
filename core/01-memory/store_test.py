#!/usr/bin/env python3
"""Agent OS memory/registry store — contract test (the definition of done).

Behavioral, like phase-0/boundary-test.sh: exercises the store against a TEMP store and
asserts the §5 schema contract + the restricted-tenant wall. Needs only pyyaml + stdlib (no mcp SDK),
so it runs regardless of the server venv. Exit 0 = contract holds; non-zero = a wall/contract breach.
"""
import json
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))

FAIL = 0


def check(cond, label):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAIL += 1


def expect_raise(exc, fn, label):
    try:
        fn()
        check(False, label + "  (no error raised)")
    except exc:
        check(True, label)
    except Exception as e:  # noqa: BLE001
        check(False, label + f"  (wrong error: {type(e).__name__}: {e})")


tmp = tempfile.mkdtemp(prefix="agentos-store-test-")
os.environ["HARNESS_WORK_STORE"] = os.path.join(tmp, "work")
os.environ["HARNESS_META_STORE"] = os.path.join(tmp, "meta")
# The search-index cache and commit-failure log both live under $AGENTOS_STATE — must be test-scoped
# too, or a test run would read/write the real ~/.local/state/agent-os cache (same tenant names as
# prod) and could transiently corrupt what a concurrent real session sees.
os.environ["AGENTOS_STATE"] = os.path.join(tmp, "state")

import agentos_store as s  # noqa: E402

print(f"\n[ store contract test  (temp store: {tmp}) ]")

# 1 — put + get round-trip
r = s.put({"type": "semantic", "title": "Coffee grind notes", "tenant": "work",
           "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
           "tags": ["coffee"], "body": "Use 18g for a double shot."})
check(bool(r["id"]), "put returns an id")
g = s.get(r["id"])
check(g["frontmatter"]["title"] == "Coffee grind notes", "get round-trips title")
check("18g" in g["body"], "get round-trips body")
check(os.path.exists(g["path"]), "note file written to disk")

# 1b — write is git-committed (audit trail), incl. git-init of the previously-bare work store
log = subprocess.run(["git", "-C", os.environ["HARNESS_WORK_STORE"], "log", "--oneline"],
                     capture_output=True, text=True)
check("memory.put" in log.stdout, "write is git-committed")

# 2 — schema validation
expect_raise(s.ValidationError,
             lambda: s.put({"type": "semantic", "tenant": "work", "sensitivity": "internal",
                            "egress": "cloud-ok", "status": "committed"}),
             "missing title rejected")
expect_raise(s.ValidationError,
             lambda: s.put({"type": "bogus", "title": "x", "tenant": "work",
                            "sensitivity": "internal", "egress": "cloud-ok", "status": "committed"}),
             "invalid type rejected")

# 3 — the RESTRICTED wall (the one hard memory wall)
expect_raise(s.RefusalError,
             lambda: s.put({"type": "episodic", "title": "secret", "tenant": "restricted",
                            "sensitivity": "restricted", "egress": "local-only",
                            "status": "committed"}),
             "restricted tenant refused on put")
expect_raise(s.RefusalError, lambda: s.get("anything", tenant="restricted"), "restricted tenant refused on get")
expect_raise(s.RefusalError, lambda: list(s.search(tenant="restricted")), "restricted tenant refused on search")
expect_raise(s.RefusalError,
             lambda: s.put({"type": "semantic", "title": "leak", "tenant": "work",
                            "sensitivity": "restricted", "egress": "local-only",
                            "status": "committed"}),
             "restricted content refused in cloud-ok store")

# 4 — search filters
s.put({"type": "episodic", "title": "Kitchen sink fix", "tenant": "meta",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "tags": ["plumbing"], "body": "Replaced the gasket."})
check(any(x["title"] == "Kitchen sink fix" for x in s.search(query="gasket")), "search by free-text body")
by_tag = s.search(tags=["coffee"])
check(by_tag and all("plumbing" not in x["tags"] for x in by_tag), "search by tag filters out non-matches")
check(all(x["type"] == "episodic" for x in s.search(type="episodic")), "search by type filters")

# 5 — typed graph link
s.link(r["id"], "relates_to", "household-grocery-list")
links = s.get(r["id"])["frontmatter"].get("links", [])
check(any(l.get("relation") == "relates_to" and l.get("target") == "household-grocery-list" for l in links),
      "memory.link adds a typed link")

# 6 — capability registry
cap = s.register({"title": "Inventory sync", "capability_type": "tool", "tenant": "work",
                  "scope": ["work"], "tags": ["inventory", "sync"],
                  "health": {"status": "healthy", "error_rate": 0.07}, "body": "v7 sync."})
check(any(f["title"] == "Inventory sync" for f in s.find(type="tool")), "registry.find by capability type")
check(len(s.find(tag="inventory")) >= 1, "registry.find by tag")
h = s.health(cap["id"], {"status": "degraded", "error_rate": 0.2})
check(h["health"]["status"] == "degraded", "registry.health updates")
check(s.health(cap["id"])["health"]["error_rate"] == 0.2, "registry.health persists")
# re-register is idempotent (stable id by title), not a duplicate
s.register({"title": "Inventory sync", "capability_type": "tool", "body": "v8."})
check(len(s.find(name="Inventory sync")) == 1, "re-register updates (no duplicate)")
# 6b — re-register is non-destructive: a body-only update keeps health + links (auto-update is safe)
s.link(cap["id"], "supersedes", "some-old-followup")
s.register({"title": "Inventory sync", "capability_type": "tool", "tenant": "work", "body": "v9."})
reg_fm = s.get(cap["id"])["frontmatter"]
check(reg_fm.get("health", {}).get("status") == "degraded", "re-register preserves health")
check(any(l.get("target") == "some-old-followup" for l in reg_fm.get("links", [])),
      "re-register preserves links")

# 6c — put() is non-destructive too (the memory_put footgun fix): a body/tags update must preserve
# frontmatter the caller didn't re-supply — capability_type, health, and child_of links. Before the
# fix, updating a feature's body via put dropped its child_of link (orphaning it) + health.
s.put({"type": "feature", "title": "Sub-feature X", "tenant": "work", "id": "feat-x",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "v1"})
s.link("feat-x", "child_of", "cap-parent", "work")
s.health("feat-x", {"status": "degraded"}, "work")
s.put({"type": "feature", "title": "Sub-feature X", "tenant": "work", "id": "feat-x",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "v2 body"})
fx = s.get("feat-x")
check(any(l.get("relation") == "child_of" and l.get("target") == "cap-parent"
          for l in fx["frontmatter"].get("links", [])),
      "put preserves child_of link on a body update (non-destructive)")
check(fx["frontmatter"].get("health", {}).get("status") == "degraded", "put preserves health on a body update")
check("v2 body" in fx["body"], "put still updates the body")
# capability_type survives a body-only put on a capability (so find()/registry stay correct)
s.put({"type": "capability", "title": "Inventory sync", "tenant": "work", "id": cap["id"],
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "body-only via put"})
check(s.get(cap["id"])["frontmatter"].get("capability_type") == "tool",
      "put preserves capability_type on a capability body update")

# 6d — unlink removes a typed link (inverse of link); needed to drop a stale child_of edge
s.unlink("feat-x", "child_of", "cap-parent", "work")
check(not any(l.get("target") == "cap-parent" for l in s.get("feat-x")["frontmatter"].get("links", [])),
      "memory.unlink removes the link")
s.unlink("feat-x", "child_of", "cap-parent", "work")  # idempotent: no error on a missing link
check(True, "memory.unlink is a no-op when the link is absent")

# 7 — passive follow-up completion: shipping work closes a follow-up without a `done` tag
fu = s.put({"type": "semantic", "title": "FOLLOW-UP: ship widget CSV export", "tenant": "meta",
            "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
            "tags": ["follow-up"], "body": "export widgets to CSV"})
check(fu["id"] in [f["id"] for f in s.followups(tenant="meta")], "new follow-up shows as open")
# search now surfaces links so the digest can see supersession without per-note lookups
s.put({"type": "episodic", "title": "Widget export shipped", "tenant": "meta",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "CSV export landed", "supersedes": [fu["id"]]})
shipped = next(x for x in s.search(query="CSV export landed", tenant="meta"))
check(any(l.get("relation") == "supersedes" for l in shipped.get("links", [])), "search returns links")
states = {f["id"]: f["state"] for f in s.followups(tenant="meta", include_closed=True)}
check(states.get(fu["id"]) == "closed", "supersedes link closes the follow-up (no done tag)")
check(fu["id"] not in [f["id"] for f in s.followups(tenant="meta")],
      "closed follow-up drops out of the open list")
# a capability update can do the same in one call (capability becomes the follow-up's home)
fu_cap = s.put({"type": "semantic", "title": "FOLLOW-UP: ship exporter dashboard", "tenant": "meta",
                "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
                "tags": ["follow-up"], "body": "dashboard for the exporter"})
s.register({"title": "Widget exporter", "capability_type": "tool", "tenant": "meta",
            "scope": ["meta"], "body": "CSV + dashboard", "supersedes": [fu_cap["id"]]})
check(fu_cap["id"] not in [f["id"] for f in s.followups(tenant="meta")],
      "registry update with supersedes closes the follow-up passively")
# partial completion: an `advances` link keeps it open but flags it partial
fu2 = s.put({"type": "semantic", "title": "FOLLOW-UP: full analytics", "tenant": "meta",
             "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
             "tags": ["follow-up"], "body": "events + dashboards + attribution"})
s.put({"type": "episodic", "title": "Events table landed", "tenant": "meta",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "events only", "advances": [fu2["id"]]})
part = {f["id"]: f["state"] for f in s.followups(tenant="meta")}
check(part.get(fu2["id"]) == "partial", "advances link marks follow-up partial (still open)")

# 8 — tenant surface excludes the restricted tenant
check(s.list_tenants() == ["meta", "work"], "list_tenants excludes restricted")

# 9 — concurrent-write safety: never block on a lock, self-heal a stale one, sweep skipped commits
import fcntl  # noqa: E402
import time  # noqa: E402
work_root = os.environ["HARNESS_WORK_STORE"]


def _commit_count(root):
    r = subprocess.run(["git", "-C", root, "rev-list", "--count", "HEAD"], capture_output=True, text=True)
    return int((r.stdout or "0").strip() or 0)


# 9a — a stale git index.lock (crashed prior commit) is cleared so commits aren't deadlocked forever
idx = os.path.join(work_root, ".git", "index.lock")
open(idx, "w").close()
os.utime(idx, (time.time() - 3600, time.time() - 3600))  # backdate so it reads as stale
before = _commit_count(work_root)
s.put({"type": "semantic", "title": "After stale lock", "tenant": "work", "sensitivity": "internal",
       "egress": "cloud-ok", "status": "committed", "body": "wrote despite stale lock."})
check(not os.path.exists(idx), "stale index.lock cleared on commit")
check(_commit_count(work_root) > before, "commit still succeeds after clearing stale lock")

# 9b — while another agent holds the commit lock, a write must NOT block or raise (file still lands)
held = open(os.path.join(work_root, ".git", "agentos-commit.lock"), "w")
fcntl.flock(held, fcntl.LOCK_EX)
r_def = s.put({"type": "semantic", "title": "Written under contention", "tenant": "work",
               "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
               "body": "no halt while commit lock is held."})
check(os.path.exists(s.get(r_def["id"])["path"]), "write succeeds while commit lock held (no halt)")
fcntl.flock(held, fcntl.LOCK_UN)
held.close()
# 9c — the next successful commit sweeps the deferred file in (self-healing audit trail)
s.put({"type": "semantic", "title": "Sweeper", "tenant": "work", "sensitivity": "internal",
       "egress": "cloud-ok", "status": "committed", "body": "this commit also captures the deferred note."})
porcelain = subprocess.run(["git", "-C", work_root, "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
check(porcelain == "", "deferred write swept into the next commit (self-healing trail)")

# 9d — multi-term search ANDs the terms (so "inventory reporting" matches a note tagged with both)
s.put({"type": "semantic", "title": "Quarterly tax filing", "tenant": "work", "sensitivity": "internal",
       "egress": "cloud-ok", "status": "committed", "tags": ["finance", "taxes"],
       "body": "Q3 estimated taxes due."})
check(any("quarterly tax" in x["title"].lower() for x in s.search(query="quarterly taxes")),
      "multi-term search ANDs terms (order-independent)")
# Query quality (task 2): when no single note contains every term, the OR-fallback still ranks in
# matches from different notes (each term hits a different note) — this used to hard-exclude under
# strict AND-only matching; the fallback is now the documented, intended behavior.
check(bool(s.search(query="quarterly plumbing")),
      "OR-fallback surfaces hits when terms match different notes, not zero results")
check(not s.search(query="zzznonexistentterm999 zzzanothernonexistent888"),
      "search still excludes when no term matches anything anywhere (true absence, AND and OR both empty)")

# 10 — relevance ranking: BM25 reorders the AND-matched set so the best hit beats the newest hit.
# Each pair inserts the strong match FIRST (older) and a weak match SECOND (newer), so a result
# that leads with the strong match proves ranking by relevance, not by recency.
s.put({"type": "semantic", "title": "Espresso extraction tuning", "tenant": "work", "id": "rank-title-hit",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "grind dose yield"})
s.put({"type": "semantic", "title": "Garden log", "tenant": "work", "id": "rank-body-only",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "tried espresso while weeding"})
ranked = [x["id"] for x in s.search(query="espresso", tenant="work")]
check(ranked[:1] == ["rank-title-hit"], "BM25 ranks a title hit above an incidental body mention (beats recency)")
s.put({"type": "semantic", "title": "bread notes", "tenant": "work", "id": "rank-tf-high",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "sourdough sourdough sourdough hydration bulk"})
s.put({"type": "semantic", "title": "bread notes 2", "tenant": "work", "id": "rank-tf-low",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "a passing sourdough thought"})
ranked_tf = [x["id"] for x in s.search(query="sourdough", tenant="work")]
check(ranked_tf[:1] == ["rank-tf-high"], "BM25 ranks high term-frequency above a single mention (beats recency)")

# 11 — concurrent-write hammer: N processes × M puts against one store. Every write must land on
# disk (writes are direct, never gated by the commit lock) and the trail must self-heal to a clean
# tree after a final sweep — no lost notes, no deadlock. This is the multi-agent reality, for real.
mp.set_start_method("fork", force=True)  # inherit env + loaded module; spawn would re-exec this script


def _hammer_worker(wid, m):
    for j in range(m):
        s.put({"type": "semantic", "title": f"hammer {wid} {j}", "tenant": "work",
               "id": f"hammer-{wid}-{j}", "sensitivity": "internal", "egress": "cloud-ok",
               "status": "committed", "body": f"concurrent write {wid}/{j}"})


N_PROC, PER = 4, 12
procs = [mp.Process(target=_hammer_worker, args=(w, PER)) for w in range(N_PROC)]
for p in procs:
    p.start()
for p in procs:
    p.join(timeout=60)
check(all(not p.is_alive() for p in procs), "all concurrent writers finished (no deadlock)")
landed = [f for f in os.listdir(os.path.join(work_root, "notes")) if f.startswith("hammer-")]
check(len(landed) == N_PROC * PER, f"all {N_PROC * PER} concurrent writes landed on disk (no lost writes)")


def _readable(w, j):
    try:
        return bool(s.get(f"hammer-{w}-{j}"))
    except Exception:
        return False


check(all(_readable(w, j) for w in range(N_PROC) for j in range(PER)), "every concurrent note is readable")
s.put({"type": "semantic", "title": "hammer sweeper", "tenant": "work", "id": "hammer-sweeper",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "final sweep commit"})
porc = subprocess.run(["git", "-C", work_root, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
check(porc == "", "after concurrent hammer the tree is fully committed (self-healing trail, nothing dropped)")

# 11b — same-note read-modify-write must be atomic across processes (a second lineage review F1, 2026-07-05).
# Test 11 hammers DISTINCT ids, so it never touches the lost-update race that F1 is about: two
# processes that each read the SAME note, append a link, and write it back. Without a per-note lock
# the second write is built on a stale read and silently drops the first's link. N processes each add
# K DISTINCT links to ONE shared note; all N*K must survive. Reliably red before the _note_lock fix
# (contention drops some), green after.
s.put({"type": "semantic", "title": "link race target", "tenant": "work", "id": "linkrace",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "shared note"})


def _link_worker(wid, k):
    for j in range(k):
        s.link("linkrace", "relates_to", f"peer-{wid}-{j}", "work")


L_PROC, L_PER = 8, 8
lprocs = [mp.Process(target=_link_worker, args=(w, L_PER)) for w in range(L_PROC)]
for p in lprocs:
    p.start()
for p in lprocs:
    p.join(timeout=60)
check(all(not p.is_alive() for p in lprocs), "all concurrent same-note linkers finished (no deadlock)")
race_targets = {l.get("target") for l in (s.get("linkrace", "work")["frontmatter"].get("links") or [])}
check(len(race_targets) == L_PROC * L_PER,
      f"all {L_PROC * L_PER} concurrent same-note links survived "
      f"(got {len(race_targets)}; no lost read-modify-write update)")

# 12 — the digest never silently truncates: with more open follow-ups than the cap, it shows the
# cap and an explicit "…and N more" line (session_context.py). Run the real hook over the temp store.
for i in range(16):
    s.put({"type": "semantic", "title": f"FOLLOW-UP: digest-cap probe {i}", "tenant": "meta",
           "id": f"digest-cap-{i}", "sensitivity": "internal", "egress": "cloud-ok",
           "status": "committed", "tags": ["follow-up"], "body": "open follow-up for the cap test"})
digest = subprocess.run([sys.executable, os.path.join(HERE, "session_context.py")],
                        capture_output=True, text=True).stdout
# Scope the count to the Open-follow-ups block (the same titles also recur in Recent notes).
open_block = digest.split("Open follow-ups:", 1)[-1].split("\n\n", 1)[0]
check(open_block.count("digest-cap probe") == 14, "digest shows exactly the follow-up cap (14), not all 16")
check("and 2 more" in open_block, "digest reports the 2 hidden follow-ups (no silent truncation)")

# 13 — query quality (task 2 acceptance): tokenized OR-fallback surfaces what strict-AND missed,
# now that search is index-backed (rewired to the SQLite/FTS5 cache).
hook_note = s.put({"type": "semantic", "title": "SessionStart hook digest", "tenant": "meta",
                   "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
                   "body": "session_context.py implements the SessionStart hook that prints the digest."})
# Section 12 above seeded 16 "FOLLOW-UP: digest-cap probe" notes in meta whose titles also
# contain "digest" — check the SPECIFIC new note's id is returned, not just that some "digest"
# title is present (which those probes would satisfy even if this note weren't matched at all).
digest_hits = s.search(query="digest session-start hook", tenant="meta")
check(any(x["id"] == hook_note["id"] for x in digest_hits),
      "index-backed search: 'digest session-start hook' returns the digest/session-context note")

s.put({"type": "semantic", "title": "Regional filing status", "tenant": "work",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "Three renewals filed this week in county court."})
renewal_hits = s.search(query="renewal filings", tenant="work")
check(any("filing status" in x["title"].lower() for x in renewal_hits),
      "index-backed search: 'renewal filings' matches 'renewals filed' via the OR fallback")

# tags filter + OR-fallback must compose: an AND-match note that the tag filter drops must still
# let the OR-fallback surface a different, correctly-tagged note (caught in review — the cache path
# was deciding AND-vs-OR on the raw SQL rows *before* the tags/since post-filter, so a tag-filtered
# empty AND set never relaxed to OR, unlike the full-scan fallback).
s.put({"type": "semantic", "title": "Release notes deploy fix", "tenant": "work",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "tags": ["misc"], "body": "deploy fix rollout"})
s.put({"type": "semantic", "title": "Release tagged note", "tenant": "work",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "tags": ["release"], "body": "deploy notes"})
tagged_hits = s.search(query="deploy fix", tags=["release"], tenant="work")
check(any(x["title"] == "Release tagged note" for x in tagged_hits),
      "tags filter composes with the OR-fallback (AND-match dropped by tag still relaxes to OR)")

# _looks_terminal regression (caught in review): the hyphen-joining added for the "prod-live"
# compound must not swallow an otherwise-plain terminal word inside any OTHER hyphenated title.
check(s._looks_terminal("Widget exporter — fully-shipped"),
      "_looks_terminal still catches a terminal word inside an unrelated hyphenated compound")
# NOTE — semantic change from the adversarial-audit fix (finding 5): LIVE is now a recognized
# terminal word (the audit flagged it as a missed case: DEPLOYED/RELEASED/LIVE/GA/... were all
# absent from the old vocabulary). Trailing "LIVE" now IS terminal; a bare LEADING/mid-title "live"
# (an ordinary descriptive use, not a status announcement) still is not — see the false-positive
# battery below for the precision side of this same fix.
check(s._looks_terminal("Service is now LIVE"),
      "_looks_terminal now catches trailing LIVE as a terminal status word (expanded vocabulary)")
check(not s._looks_terminal("Live blog transcript archive"),
      "_looks_terminal does not flag a LEADING, non-trailing 'live' (ordinary description, not a status)")

# 13b — finding 5 (supersedes enforcement precision): expanded terminal vocabulary + a trailing-
# position heuristic that fixes the auditor's over-eager false positives without losing recall on
# the missed words. Every case below is a literal auditor example (or a straightforward instance
# of a missed word in the same trailing-status shape as the words already covered).
_TERMINAL_BYPASS_TITLES = [
    "Inventory sync DEPLOYED", "Payments pipeline RELEASED", "Widget exporter GA",
    "Support ticket RESOLVED", "Migration DELIVERED", "Feature LANDED",
    "Legacy endpoint DEPRECATED", "Old exporter RETIRED", "Bug WONTFIX",
    "July release FINAL", "Now SHIPPING",
]
for _title in _TERMINAL_BYPASS_TITLES:
    check(s._looks_terminal(_title), f"_looks_terminal catches the expanded word in {_title!r}")

_TERMINAL_FALSE_POSITIVE_TITLES = [
    "Complete the RentCast migration",
    "Investigate why the connection closed",
    "Bug: modal closed on outside click",
    "Finished-goods inventory tracker",
]
for _title in _TERMINAL_FALSE_POSITIVE_TITLES:
    check(not s._looks_terminal(_title), f"_looks_terminal does NOT flag the auditor false-positive {_title!r}")

# register() must actually let the false-positive titles through (not just _looks_terminal in
# isolation) — the end-to-end acceptance criterion, with supersedes omitted.
_fp_reg = s.register({"title": "Complete the RentCast migration", "capability_type": "tool",
                       "tenant": "work", "body": "still in progress"})
check(bool(_fp_reg.get("id")),
      "register() accepts an imperative-verb-leading title with supersedes omitted (no false block)")

# 13c — regression caught in independent review: the trailing-only precision fix above must not
# lose recall on a LEADING status-TAG convention ("SHIPPED: ...", "[DONE] ...", "COMPLETE - ..."),
# which the old any-position matcher used to catch. A separator (colon/dash/brackets) right after
# the leading word is what makes it a tag rather than an imperative-verb-plus-object continuation
# ("Complete the ..." has no such separator and must still pass, checked above).
_LEADING_TAG_TITLES = [
    "SHIPPED: New checkout flow",
    "DONE - Migrate to new schema",
    "[COMPLETE] Widget exporter",
]
for _title in _LEADING_TAG_TITLES:
    check(s._looks_terminal(_title), f"_looks_terminal catches a LEADING status tag in {_title!r}")
expect_raise(ValueError,
             lambda: s.register({"title": "SHIPPED: New checkout flow", "capability_type": "tool",
                                  "tenant": "work"}),
             "register() still blocks a LEADING-tag terminal title with supersedes omitted (no silent bypass)")

# precedence, also caught in review: the leading-task-word suppression takes priority over every
# other signal, including the position-agnostic prod-live compound — "Investigate ..." reads as an
# open task regardless of what it mentions.
check(not s._looks_terminal("Investigate prod-live outage"),
      "_looks_terminal: a leading task word suppresses even the prod-live compound signal")

# 14 — supersedes enforcement (task 3): a terminal-looking title blocks register() unless
# supersedes is explicit; a title-less PATCH re-register is never gated; put() only warns.
s.put({"type": "semantic", "title": "FOLLOW-UP: wire the billing sync retry", "tenant": "work",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "tags": ["inventory", "sync"], "body": "retry on sync timeout"})
expect_raise(ValueError,
             lambda: s.register({"title": "Inventory sync SHIPPED", "capability_type": "tool",
                                  "tenant": "work", "tags": ["inventory", "sync"], "body": "v10"}),
             "register() blocks a terminal title with supersedes omitted")
ok = s.register({"title": "Inventory sync SHIPPED", "capability_type": "tool", "tenant": "work",
                  "tags": ["inventory", "sync"], "body": "v10", "supersedes": []})
check(bool(ok.get("id")), "register() allows a terminal title when supersedes=[] is passed explicitly")
patched = s.register({"id": ok["id"], "capability_type": "tool", "tenant": "work",
                       "health": {"status": "healthy"}})
check(patched.get("id") == ok["id"],
      "register() never gates a title-less PATCH re-register (health sweep keeps working)")

warned = s.put({"type": "episodic", "title": "Widget CSV export DONE", "tenant": "meta",
                "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
                "body": "shipped it"})
check("warning" in warned, "put() attaches a non-blocking warning for a terminal title with no supersedes")
no_warn = s.put({"type": "episodic", "title": "Widget CSV export DONE", "tenant": "meta",
                  "id": warned["id"], "sensitivity": "internal", "egress": "cloud-ok",
                  "status": "committed", "body": "shipped it", "supersedes": []})
check("warning" not in no_warn, "put() does not warn once supersedes=[] is passed explicitly")

# 15 — commit-failure surfacing (task 4): a forced git failure appends to commit-failures.jsonl,
# never raising from the writer (the note itself always lands) or from the logger itself.
commit_log_path = os.path.join(os.environ["AGENTOS_STATE"], "commit-failures.jsonl")
git_dir = os.path.join(work_root, ".git")
os.chmod(git_dir, 0o500)  # strip write: git can't create index.lock, so add/commit fails
try:
    forced = s.put({"type": "semantic", "title": "Forced commit failure probe", "tenant": "work",
                     "sensitivity": "internal", "egress": "cloud-ok", "status": "committed", "body": "x"})
finally:
    os.chmod(git_dir, 0o700)
check(bool(s.get(forced["id"])["path"]), "note file still lands on disk despite the forced commit failure")
check(os.path.exists(commit_log_path), "commit-failures.jsonl is created on a forced git failure")
if os.path.exists(commit_log_path):
    rec = json.loads(open(commit_log_path).read().strip().splitlines()[-1])
    check(rec.get("tenant") == "work" and "error" in rec and "ts" in rec,
          "commit-failure line carries ts/tenant/error per the shared-contract format")

# 16 — adversarial-audit finding 1 (search recall / cache-fallback parity): an earlier cache
# implementation matched query terms as FTS5 PREFIXES (`"term"*`), a strict subset of the
# full-scan fallback's SUBSTRING semantics — it silently dropped every note where the term only
# appears mid-word (e.g. "conciliation" never matching "reconciliation"), a 22-57% recall loss in
# practice. Both paths must now return the SAME result set, and it must be the recall-maximizing
# substring semantics. Each probe note below contains its query term ONLY embedded inside a longer
# word (never as a whole word or a word-initial prefix), so a match proves true substring recall.
s.put({"type": "semantic", "title": "County filing note", "tenant": "work", "id": "recall-conciliation-probe",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "Filed a reconciliation notice with the county clerk this week."})
s.put({"type": "semantic", "title": "Vendor address note", "tenant": "work", "id": "recall-lookup-probe",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "Notes on outskips of the county and how we retraced the filer's address."})
s.put({"type": "semantic", "title": "Ops note", "tenant": "work", "id": "recall-sessionstart-probe",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "Team obsession with the pipeline continues; this is the upstart phase of the rollout."})
s.put({"type": "semantic", "title": "Debug note", "tenant": "work", "id": "recall-memoryput-probe",
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "Debugging thememoryleak issue overnight; the rest is putative and unconfirmed."})

_RECALL_QUERIES = {
    "conciliation": "recall-conciliation-probe",
    "skip-trace": "recall-lookup-probe",
    "session-start": "recall-sessionstart-probe",
    "memory_put": "recall-memoryput-probe",
}
for _q, _expected_id in _RECALL_QUERIES.items():
    _cache_hits = {row["id"] for row in s.search(query=_q, tenant="work", limit=1000)}
    _scan_hits = {row["id"] for row in
                  s._search_scan("work", s._TOKEN_RE.findall(_q.lower()), set(), None, None)}
    check(_expected_id in _cache_hits,
          f"cache-backed search recalls a mid-word substring match for {_q!r} (auditor's exact query)")
    check(_cache_hits == _scan_hits,
          f"cache result set == full-scan-fallback result set for query {_q!r} (no silent recall gap)")

# 17 — finding 2 (read-time restricted backstop): the write wall can't stop a restricted
# note that arrives out-of-band (manual file drop / git merge into work or meta) — only a
# read-time check can. get() must refuse it; search() (cache AND fallback) must omit it; its body
# must never sit as plaintext in the search-cache sqlite file, not even transiently.
# It also carries an outgoing `supersedes` link so the _link_index fallback repro below (17b(ii))
# has something to actually detect a leak with — a note with no links can never appear as a
# "closer" regardless of whether it's filtered.
restricted_leak_path = os.path.join(work_root, "notes", "restricted-leak-probe.md")
with open(restricted_leak_path, "w", encoding="utf-8") as f:
    f.write("---\n"
            "id: restricted-leak-probe\n"
            "type: episodic\n"
            "title: Family leak probe UNIQUETOKEN8675309\n"
            "tenant: work\n"
            "sensitivity: restricted\n"
            "egress: local-only\n"
            "status: committed\n"
            "links:\n"
            "  - relation: supersedes\n"
            "    target: restricted-leak-supersedes-target\n"
            "---\n\n"
            "This body must never be indexed or served: UNIQUETOKEN8675309.\n")
expect_raise(s.RefusalError, lambda: s.get("restricted-leak-probe"),
             "get() refuses a restricted note that arrived out-of-band in a cloud-ok store")
leak_hits = s.search(query="UNIQUETOKEN8675309", tenant="work", limit=50)
check(not any(x["id"] == "restricted-leak-probe" for x in leak_hits),
      "cache-backed search() never returns the out-of-band restricted note")
scan_leak_hits = s._search_scan("work", ["uniquetoken8675309"], set(), None, None)
check(not any(x["id"] == "restricted-leak-probe" for x in scan_leak_hits),
      "full-scan fallback also never returns the out-of-band restricted note")
_work_cache_file = s._cache_path("work")
if _work_cache_file.exists():
    check(b"UNIQUETOKEN8675309" not in _work_cache_file.read_bytes(),
          "restricted body is never indexed as plaintext into the search-cache sqlite file")
else:
    check(True, "restricted body is never indexed as plaintext (no cache file materialized)")

# 17b — regressions caught in independent review: two OTHER read paths that resolve a note by id
# without going through get() must also refuse/omit an out-of-band restricted note.
# (i) put()/register() carrying an existing note's frontmatter forward on an update must never
# silently "launder" a restricted note into an ordinary one — refuse outright instead.
expect_raise(s.RefusalError,
             lambda: s.put({"type": "episodic", "title": "relabeled", "id": "restricted-leak-probe",
                            "tenant": "work", "sensitivity": "internal", "egress": "cloud-ok",
                            "status": "committed", "body": "trying to demote it"}),
             "put() refuses to update/carry-over an existing restricted note (no silent demotion)")
expect_raise(s.RefusalError,
             lambda: s.register({"id": "restricted-leak-probe", "title": "relabeled cap",
                                 "capability_type": "tool", "tenant": "work"}),
             "register() refuses to update/carry-over an existing restricted note")
# (ii) _link_index's non-cache fallback (used when sqlite/FTS is unavailable) must not leak the
# restricted note's id into followups()'s closed_by/advanced_by either.
_orig_sqlite3 = s.sqlite3
s.sqlite3 = None  # force every cache path off, exercising _link_index's _iter_notes fallback
try:
    fallback_closed, fallback_advanced = s._link_index(["work"])
finally:
    s.sqlite3 = _orig_sqlite3
check("restricted-leak-probe" not in fallback_closed and
      not any("restricted-leak-probe" in v for v in fallback_closed.values()) and
      "restricted-leak-probe" not in fallback_advanced and
      not any("restricted-leak-probe" in v for v in fallback_advanced.values()),
      "_link_index's non-cache fallback never surfaces the restricted note's id")

# 18 — finding 3 (cache doesn't gate restricted): a restricted-tenant cache file must never be created,
# not even as a transient side effect of a request that's ultimately refused higher up the stack.
restricted_cache_path = s._cache_path("restricted")
expect_raise(s.RefusalError, lambda: list(s.search(tenant="restricted", query="anything")),
             "search(tenant='restricted') is refused")
check(not restricted_cache_path.exists(),
      "search(tenant='restricted') never creates fts/restricted.sqlite, even transiently")
expect_raise(s.RefusalError, lambda: s.followups(tenant="restricted"),
             "followups(tenant='restricted') is refused")
check(not restricted_cache_path.exists(), "followups(tenant='restricted') never creates fts/restricted.sqlite either")

# 19 — finding 4 (cross-tenant id collision -> wrong-store writes): the same id registered
# independently in two tenants must never let a tenant-less WRITE silently pick one and leave the
# other stale. health()/link()/unlink() must raise AmbiguousIdError and demand an explicit tenant;
# an explicit tenant still resolves cleanly.
collide_id = "cap-collide-example"
s.register({"id": collide_id, "title": "Collider work copy", "capability_type": "tool",
            "tenant": "work", "health": {"status": "healthy"}})
s.register({"id": collide_id, "title": "Collider meta copy", "capability_type": "tool",
            "tenant": "meta", "health": {"status": "degraded"}})
expect_raise(s.AmbiguousIdError, lambda: s.health(collide_id, {"status": "failing"}),
             "health() refuses a tenant-less write when the id collides across tenants")
check(s.get(collide_id, tenant="work")["frontmatter"]["health"]["status"] == "healthy",
      "work copy's health is untouched after the refused ambiguous write (no silent wrong-store write)")
expect_raise(s.AmbiguousIdError, lambda: s.link(collide_id, "relates_to", "some-target-id"),
             "link() refuses a tenant-less write when from_id collides across tenants")
expect_raise(s.AmbiguousIdError, lambda: s.unlink(collide_id, "relates_to", "some-target-id"),
             "unlink() refuses a tenant-less write when from_id collides across tenants")
ok_health = s.health(collide_id, {"status": "failing"}, tenant="meta")
check(ok_health["health"]["status"] == "failing", "health() with an explicit tenant resolves the collision cleanly")
check(s.get(collide_id, tenant="work")["frontmatter"]["health"]["status"] == "healthy",
      "work copy remains untouched by the explicitly meta-scoped write")

# 20 — finding 6 (store-side injection defense-in-depth): a caller-supplied id/relation/target
# containing characters an injection needs (quotes, angle brackets, spaces, slashes) is rejected;
# every character actually used by existing ids is still accepted (verified separately against all
# 707 live work+meta ids on a temp copy of the real stores before this charset was chosen —
# see the fix commit message).
expect_raise(s.ValidationError,
             lambda: s.put({"type": "semantic", "title": "XSS probe", "tenant": "work",
                            "id": 'x"><img src=x onerror=alert(1)>', "sensitivity": "internal",
                            "egress": "cloud-ok", "status": "committed"}),
             "put() rejects a caller-supplied id containing quotes/angle-brackets")
expect_raise(s.ValidationError,
             lambda: s.register({"title": "XSS cap probe", "capability_type": "tool",
                                 "id": "cap/../../etc", "tenant": "work"}),
             "register() rejects a caller-supplied id containing a slash")
safe_ok = s.put({"type": "semantic", "title": "Safe id probe", "tenant": "work",
                  "id": "safe-id_with.dots-and_underscores123", "sensitivity": "internal",
                  "egress": "cloud-ok", "status": "committed", "body": "fine"})
check(safe_ok.get("id") == "safe-id_with.dots-and_underscores123",
      "put() accepts an id using the full safe charset (letters/digits/dot/dash/underscore)")
expect_raise(s.ValidationError,
             lambda: s.link(r["id"], 'relates_to"; DROP TABLE', "some-target"),
             "link() rejects a relation containing characters outside the relation charset")
expect_raise(s.ValidationError,
             lambda: s.link(r["id"], "relates_to", 'target"><script>alert(1)</script>'),
             "link() rejects a target that is not a syntactically valid id")

# 20b — regression caught in independent review: a bad supersedes/advances target used to fail
# only when link() ran AFTER the primary note was already written+committed, leaving a partial
# state (note exists, link never drawn, caller sees an exception). put() must now validate those
# targets up front and reject the WHOLE call before writing anything.
_bad_target_id = "put-atomic-probe"
expect_raise(s.ValidationError,
             lambda: s.put({"type": "episodic", "title": "Atomic probe", "tenant": "work",
                            "id": _bad_target_id, "sensitivity": "internal", "egress": "cloud-ok",
                            "status": "committed", "body": "x",
                            "supersedes": ['bad"target']}),
             "put() rejects a bad supersedes target before writing anything")
_probe_landed = False
try:
    s.get(_bad_target_id)
    _probe_landed = True
except s.NotFound:
    _probe_landed = False
check(not _probe_landed,
      "put() with an invalid supersedes target is fully atomic (note never lands, no partial write)")

# 21 — finding 7 (stale content on (mtime,size)-preserving edits): a same-size body edit that
# forces mtime_ns AND size back to their pre-edit values (what cp -p/rsync -a/some restores do) is
# a documented residual the incremental stat-scan cannot detect — but rebuild() (equivalent to
# deleting the cache file) forces a full reindex that does pick it up.
stale_id = "stale-mtime-probe"
s.put({"type": "semantic", "title": "Stale probe", "tenant": "work", "id": stale_id,
       "sensitivity": "internal", "egress": "cloud-ok", "status": "committed",
       "body": "original body ORIGINALTOKEN111"})
check(any(x["id"] == stale_id for x in s.search(query="ORIGINALTOKEN111", tenant="work")),
      "stale-mtime probe is indexed before the (mtime,size)-preserving edit")
stale_path = s.get(stale_id)["path"]
st_before = os.stat(stale_path)
check(len("ORIGINALTOKEN111") == len("UPDATEDTOKEN2222"), "probe tokens are deliberately same-length")
with open(stale_path, "r+", encoding="utf-8") as f:
    content = f.read().replace("ORIGINALTOKEN111", "UPDATEDTOKEN2222")
    f.seek(0)
    f.write(content)
    f.truncate()
os.utime(stale_path, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))  # force mtime_ns back exactly
check(os.stat(stale_path).st_size == st_before.st_size, "edit preserved the file size")
check(os.stat(stale_path).st_mtime_ns == st_before.st_mtime_ns, "edit preserved mtime_ns (forced, like cp -p)")
stale_hits = s.search(query="UPDATEDTOKEN2222", tenant="work")
check(not any(x["id"] == stale_id for x in stale_hits),
      "(documented residual) an (mtime_ns,size)-preserving edit is invisible to the incremental sync")
removed = s.rebuild("work")
check(bool(removed), "rebuild('work') deletes the cached sqlite file")
rebuilt_hits = s.search(query="UPDATEDTOKEN2222", tenant="work")
check(any(x["id"] == stale_id for x in rebuilt_hits),
      "after rebuild() the updated body is indexed and searchable (mitigation confirmed)")

# 22 — warm-search timing (audit deliverable evidence, not a tight perf gate). Absolute ms/call is
# environment-dependent (shared/loaded CI boxes can be 10x a quiet laptop — the per-call sqlite
# connect/WAL-setup overhead this measures is pre-existing, unchanged by this fix), so this is a
# sanity ceiling against a GROSS regression, not a perf budget. The actual no-regression evidence is
# an interleaved old-vs-new A/B done separately (same machine, same moment, so shared noise cancels
# out): median 673ms/call (new) vs 783ms/call (old) over 15 interleaved rounds — i.e. this fix did
# not make warm search slower.
warm_start = time.time()
for _ in range(20):
    s.search(query="release notes deploy", tenant="work")
warm_ms = (time.time() - warm_start) / 20 * 1000
n_notes = len(os.listdir(os.path.join(work_root, "notes")))
print(f"  [timing] warm cached search: {warm_ms:.2f} ms/call avg over 20 calls ({n_notes} notes in work)")
check(warm_ms < 3000, "warm cached search stays well under a loose sanity ceiling (no gross regression)")

# --- non-destructive body: a title-less health-only re-register (the hourly health_sweep pattern)
# must NOT blank an existing capability's documentation body ------------------------------------
s.register({"id": "cap-bodyguard", "tenant": "meta", "title": "Bodyguard",
            "capability_type": "tool", "body": "DOCS that must survive a health PATCH"})
_b_before = s.get("cap-bodyguard", "meta")["body"]
s.register({"id": "cap-bodyguard", "tenant": "meta", "health": {"status": "healthy", "note": "p"}})
_b_after = s.get("cap-bodyguard", "meta")["body"]
check(_b_after.strip() == "DOCS that must survive a health PATCH" and _b_after == _b_before,
      "title-less health-only re-register preserves the existing body (health_sweep can't wipe docs)")

# --- A1: STATUS IS HONORED AT RETRIEVAL (PAB charter 2026-08-16, ruling R5) -------------------
# `quarantined` and `archived` were valid status values that NOTHING honored at read time: both
# _search_cached and _search_scan filtered on sensitivity/tags/type/since and never read `status`,
# and the cached path could not have without a query change because the column was never SELECTed.
# That made the operator's stated flip-back for machine-written notes ("stamp them, quarantine
# them") an illusion — a quarantined note came back from search exactly like a committed one.
# Retirement has to work BEFORE anything starts writing notes automatically.
s.put({"tenant": "work", "type": "semantic", "title": "Zorblat retrieval marker committed",
       "body": "zorblat committed sentinel", "status": "committed",
       "sensitivity": "internal", "egress": "cloud-ok"})
s.put({"tenant": "work", "type": "semantic", "title": "Zorblat retrieval marker quarantined",
       "body": "zorblat quarantined sentinel", "status": "quarantined",
       "sensitivity": "internal", "egress": "cloud-ok"})
s.put({"tenant": "work", "type": "semantic", "title": "Zorblat retrieval marker archived",
       "body": "zorblat archived sentinel", "status": "archived",
       "sensitivity": "internal", "egress": "cloud-ok"})

_z_default = [r["title"] for r in s.search(query="zorblat", tenant="work", limit=50)]
check(any("committed" in t for t in _z_default),
      "A1: a committed note is returned by default search")
check(not any("quarantined" in t for t in _z_default),
      "A1: a QUARANTINED note is ABSENT from default search (the flip-back actually works)")
check(not any("archived" in t for t in _z_default),
      "A1: an ARCHIVED note is ABSENT from default search")

_z_incl = [r["title"] for r in s.search(query="zorblat", tenant="work",
                                        include_retired=True, limit=50)]
check(any("quarantined" in t for t in _z_incl) and any("archived" in t for t in _z_incl),
      "A1: include_retired=True brings quarantined AND archived back (opt-in, not deletion)")
check(any("committed" in t for t in _z_incl),
      "A1: include_retired=True still returns committed notes")

# Both read paths must agree. _search_cached is used when the sqlite cache is available and
# _search_scan is the fallback; filtering in only one means the guarantee silently depends on
# whether a cache file happened to exist.
_z_scan = [r["title"] for r in s._search_scan("work", ["zorblat"], set(), None, None)]
check(not any("quarantined" in t for t in _z_scan) and not any("archived" in t for t in _z_scan),
      "A1: the SCAN fallback path filters status too (not just the cached path)")
_z_cached = s._search_cached("work", ["zorblat"], set(), None, None)
if _z_cached is not None:
    check(not any("quarantined" in r["title"] for r in _z_cached)
          and not any("archived" in r["title"] for r in _z_cached),
          "A1: the CACHED path filters status too")

# A note with no status at all must not vanish. Legacy notes predate the field; defaulting a
# missing status to "retired" would silently empty the store's own history.
_legacy = os.path.join(work_root, "notes", "legacy-no-status.md")
with open(_legacy, "w", encoding="utf-8") as fh:
    fh.write("---\ntype: semantic\ntitle: Zorblat legacy no status\ntenant: work\n"
             "sensitivity: internal\negress: cloud-ok\n---\n\nzorblat legacy sentinel\n")
_z_legacy = [r["title"] for r in s.search(query="zorblat", tenant="work", limit=50)]
check(any("legacy" in t for t in _z_legacy),
      "A1: a note with NO status field is still returned (missing != retired)")

# --- A2: PIN THE RESTRICTED WALL AT BOTH READ PATHS ------------------------------------------------
# restricted content is already excluded at both paths, but nothing pinned it. Loop hooks are
# about to start querying the store on the operator's behalf, so the wall needs a test that fails
# if either guard is ever removed — a code-reading assurance is not a guarantee.
_fam = os.path.join(work_root, "notes", "famwall-probe.md")
with open(_fam, "w", encoding="utf-8") as fh:
    fh.write("---\ntype: semantic\ntitle: Zorblat famwall probe\ntenant: work\n"
             "sensitivity: restricted\negress: local-only\nstatus: committed\n---\n\n"
             "zorblat famwall sentinel\n")

check(not any("famwall" in r["title"] for r in s.search(query="zorblat", tenant="work", limit=50)),
      "A2: restricted content is absent from default search")
check(not any("famwall" in r["title"] for r in s.search(query="zorblat", tenant="work",
                                                       include_retired=True, limit=50)),
      "A2: include_retired does NOT become a bypass for the restricted-tenant wall")
check(not any("famwall" in r["title"]
              for r in s._search_scan("work", ["zorblat"], set(), None, None)),
      "A2: the SCAN path excludes restricted")
_fam_cached = s._search_cached("work", ["zorblat"], set(), None, None)
if _fam_cached is not None:
    check(not any("famwall" in r["title"] for r in _fam_cached),
          "A2: the CACHED path excludes restricted")

print()
if FAIL == 0:
    print("RESULT: PASS — memory/registry store contract holds.")
    sys.exit(0)
print(f"RESULT: FAIL — {FAIL} failure(s).")
sys.exit(1)
