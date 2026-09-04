# core/05-lanes — the parallel-work board

    lanes board                 # what is running, what is queued, what waits on you
    lanes needs-you             # only what is gated on you, each with the command that clears it

## The problem

Two of them, and they are the same problem seen from opposite ends.

**Outward.** You are asked, in some form, *what is happening to my request* — and that question
has no home. It lives in your head, in four threads, and in whatever a given session happened to
mention, so the only reliable way to answer it is to rebuild the answer from scratch every time.

**Inward.** Nothing stops two agents working the same files at once. The moment you open a second
Claude Code window you are running two writers against one checkout, coordinated by nothing but
your memory of what the other one was doing.

A lane answers both. Work is organized into parallel workstreams; exactly one worker holds a lane
at a time; claiming one declares the surfaces it will touch, and the claim is refused when those
surfaces overlap a lane somebody already holds.

## The model

**Lanes** are workstreams, defined as notes (`lane-def-l1`) with a rank inside a series. The
default series are `L` line work (the queue that competes for your attention), `O` ongoing
background work, and `R` research and tooling — renamed and extended in `lanes.json`.

**Locks** (`lane-lock-l1`) say who holds a lane, what they are doing, which surfaces they touch,
and when their lease expires. An expired lease frees the lane: a dead agent must not hold one
forever, because a board that shows stale holds is a board people learn to ignore.

**Items** (`lane-item-l1-<slug>`) are pieces of work, moving proposed → approved → in-progress →
done. `blocked_on` is an overlay rather than a status, so a blocked item stays visible instead of
disappearing into a state nobody reads.

**Incidents** are a second kind of item, and keeping them distinct is the point. An incident is a
thing that is *broken*; a work item is a thing awaiting a *decision*. Merged into one queue, the
list you check for "what needs me" fills with machine noise and you stop checking it. So an
incident never enters the approval queue, never counts against the awaiting-you budget, and never
closes on a yes — only on evidence that the condition cleared.

## The three rules that make it worth the trouble

**A lock is read back after it is written.** A silently-failed lock is worse than no lock, because
it reports coordination that is not happening. Every write here re-reads and says so.

**An inference never masquerades as a verified fact.** `reconcile` closes an item only on a hard,
re-checkable reference the item carries — a merged PR number, or a branch provably merged into the
trunk. Slug similarity is a *hint*: surfaced for a person, never a closing signal. And when the
check could not run at all, that is reported as its own state. An empty result from a check that
never ran is indistinguishable from a clean board, and collapsing the two is how a gate quietly
becomes decoration.

**Approval is recorded, never inferred.** `approve` demands evidence with a source prefix, and an
unattended session (`HARNESS_UNATTENDED=1`) cannot approve at all. The gate exists precisely so an
agent cannot walk through it. Revising an item's scope invalidates its approval, so rewritten work
cannot inherit an old yes.

## Configuration

`~/harness/lanes.json`, overridable with `HARNESS_LANES_CONFIG`. `lanes config` prints what is in
force. Everything site-specific lives there and nowhere else, because the alternative is a fork.

```json
{
  "repo": "~/work/analytics",
  "trunk": "origin/main",
  "pr_cli": "gh",
  "series": [
    {"key": "L", "label": "Line work — the main queue", "contended": true, "ttl_days": 14},
    {"key": "O", "label": "Ongoing — background", "contended": false, "ttl_days": 21},
    {"key": "R", "label": "Research / tooling", "contended": false, "ttl_days": 21}
  ],
  "agent_auto_approve_tiers": ["green"],
  "budget": {"awaiting_operator": 10, "open_followups": 150}
}
```

`repo` is what makes "did this ship" answerable. Without it every ground-truth check reports
SKIPPED — loudly, and it will not block you, but the board stops being able to close anything by
itself. `pr_cli` is any `gh`-compatible command; set it to `null` on a host without one and
branch-ancestry detection carries the load alone.

`agent_auto_approve_tiers` is the one setting worth thinking about twice. An agent-filed item at a
pre-cleared tier enters `approved` without a click. Approval is only dispatch-eligibility, so this
does not ship anything — but it does drop the item off `needs-you`, and the bucket that looks
routine at intake is exactly the bucket you most want to see. Keep it narrow, or set it to `[]`.

## A working day

```bash
lanes lane-add L1 --title "Customer dimension migration" --rank 1
lanes add L1 --title "Rebuild dim_customer" --what "Rewrite for the new source key" --origin me
lanes pick                                     # claims the lane and the item in one step
lanes item-set <id> --artifact 'PR #412'       # so reconcile can close it without you
lanes needs-you                                # what is actually gated on you
lanes reconcile --apply                        # close what provably shipped
```

## Tests

    ~/harness/.venv/bin/python -m pytest ~/harness/core/05-lanes/test_lanes.py -q

Thirty-six tests, none of which check that a lane can be claimed. They check the four claims that
would make this component worse than nothing if they were false: the read-back, the collision
refusal, the approval gate, and the shipped-detector's refusal to guess.

## What this does not do

It does not schedule, estimate, or tell you what to work on. The priority register is stacked by
hand on purpose — an automatically ordered list is a list nobody argues with, and the argument is
the value.

It coordinates *agents against files*, not people against people. The collision check compares the
surfaces your own sessions declare; it knows nothing about what a colleague is editing right now,
and there is no feed that would tell it. Two of your sessions cannot collide silently. You and a
teammate still can.

And it has no notion of anyone else's calendar. A lane is a workstream **you** control, ordered by
rank and arrival. Inbound questions pinned to somebody else's deadline are a different shape and
want a different tool — `menu/question-desk` specifies it. Do not stretch a lane to cover one.

The rest of the limits, including the ones the kit argued against itself over, are in
`99-notes/limits.md` §3, §4 and §9.
