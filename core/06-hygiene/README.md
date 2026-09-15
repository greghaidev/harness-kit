# core/06-hygiene — the retirement path

    hygiene status                 # one line; cheap enough for every session start
    hygiene sweep                  # DETECT — mechanical, changes nothing
    hygiene reconcile [--apply]    # DISPOSE — closes only what it can corroborate

## The problem

Every other component in this kit creates. The store keeps notes, the journal keeps days, the
lanes keep items, and the Stop guard makes sure a session records its conclusion before it ends.
Nothing retires any of it.

That asymmetry is not untidiness, it is a correctness problem, and it arrives on a schedule. A
store with a creating force and no closing force fills with items that read as open and are not.
The first time you cannot tell those apart, you stop trusting the list — and a follow-up list you
do not trust is worse than no list, because it still costs you the reading.

The measured shape of it, from the harness this component was extracted from: a follow-up pile
reached 231 open items while a weekly detection sweep ran green the whole time. About half had
never been actions at all. They were status notes — "SHIPPED", "sprint summary" — tagged
follow-up out of habit, and **a note with nothing to do can never be done**, so it sits there
permanently. Detection had found them over and over. Nothing closed them, because closing needs a
judgment about evidence and the sweep had none.

So this is two tiers, and keeping them separate is the design.

## What it looks at

| surface | what goes wrong with it |
|---|---|
| open follow-ups | status notes mis-tagged as actions; work that shipped and was never closed; items whose referenced work was abandoned; items nobody has looked at in three weeks |
| capabilities | a registration titled as complete that supersedes nothing — it reads finished here while leaving its work open somewhere else, permanently |
| **conclusions** | a conclusion on the record with **no source**, and a conclusion whose **source no longer exists** |
| your working agreements | documents that still describe a way of working you have retired |

The conclusions row is the one that makes this component belong to *this* kit rather than a
builder's. The residue risk here is not a dropped task, it is a number that outlives the evidence
under it — still being quoted after its source moved, changed, or stopped existing. Neither check
can tell you a conclusion is wrong. They tell you it can no longer be **defended**, which is a
different and far more answerable question.

A source that names a file is checked. A source that reads "the Q3 finance pack, page 4" is a
real source and is not checkable here, so nothing is said about it. Inventing a verdict for it
would be worse than the silence.

## The rule the disposition tier is built around

**An inference must never masquerade as a verified fact.** Same rule the lane board enforces for
items, here for the same reason and learned the same expensive way.

The obvious closing signal — *this follow-up mentions a PR, and that PR is merged* — is wrong
often enough to be dangerous. A note citing a PR as the **origin** of a finding looks identical to
one whose work that PR shipped. Measured on a real store: of 15 notes offered as "cites a merged
PR", 15 were false. One mentioned a PR only to say a neighboring surface was already fine; that PR
changed 54 files and not one of them was the file the note was about. Closing on the bare citation
would have silently buried a live action.

So a candidate clears three gates before it closes, and each gate is there because it caught
something real:

1. **Token corroboration.** A distinctive token from the note's stated action must appear in what
   the PR actually *changed* — its title or its changed paths. Identifier-shaped tokens
   (`etl/dim_customer.sql`) are near-proof; ordinary English words corroborate only in numbers,
   and never when the note names a file the PR did not touch.
2. **Contemporaneity.** A PR that merged within a day of the note being written cannot be that
   note's resolution. Three candidates that cleared gate 1 turned out to have been written by the
   very session that shipped the PR, recording what it deliberately did *not* do.
3. **The note's own words.** A body saying "deliberately did not touch this" or "left open" is the
   strongest possible statement that the cited PR is not closing evidence — and it is sitting in
   plain text. Both survivors of gates 1 and 2 said exactly that.

Anything that fails a gate stays **open**, and is printed by name with the reason it failed.
Bias-to-escalate beats bias-to-close: a missed close costs one line of noise, a wrong close costs
a commitment you will never learn you dropped.

Two more refusals worth stating plainly:

- **CLOSED is not MERGED.** A closed-unmerged PR means the work was abandoned, so its follow-up is
  probably still live. Those are surfaced for a re-cut or a drop, never closed.
- **An unrunnable check reports itself.** With no repo or no PR CLI configured, `reconcile` exits
  non-zero saying it could not corroborate anything, and `sweep` writes SKIPPED into its report.
  An empty result from a check that never ran is indistinguishable from a clean store, and
  collapsing the two is how a gate quietly becomes decoration.

## What it deliberately does not do

**It does not check lane items.** `lanes reconcile` already does, against the same ground truth. A
second implementation of one rule guarantees the two will disagree and that nobody will know which
is authoritative.

**It never rewrites a note.** A close is a *supersession* — a new note pointing at what it closes —
so the trail survives and the store keeps its append-only property. Nothing is ever hand-marked
`done`; the store derives follow-up state from those links on its own.

**It does not decide anything on its own schedule.** The sweep is safe to automate because it
changes nothing. `reconcile --apply` is not automated anywhere in this kit, and should not be.

## Configuration

It reads the **same `lanes.json`** the board reads, because both need the same three facts — which
repository, which trunk, which PR CLI — and two config files answering one question is how a system
ends up with two answers.

```json
{
  "repo": "~/work/analytics",
  "pr_cli": "gh",
  "hygiene": {
    "followup_stale_days": 21,
    "heartbeat_alarm_days": 8,
    "retired_phrases": [],
    "agreement_docs": ["~/.claude/CLAUDE.md"]
  }
}
```

`hygiene config` prints what is in force. `pr_cli` accepts an argument list as well as a program
name, exactly as the board's does (see the lanes README).

**`retired_phrases` ships empty, and the sweep says so in its report** rather than printing a clean
section. Yours are specific to you — the name of a process you abandoned, a tool you stopped using,
a review step that no longer exists. Fill it in when you retire something, and the documents that
still describe the old way surface the next Monday instead of teaching a new agent to work the way
you used to.

## Running it

`hygiene status` is a single line meant for the SessionStart hook, and it never touches the
network or the store — it reads one small file the last sweep wrote. If the store is missing
entirely it still prints and still exits 0, deliberately: a hook that dies noisily is a hook you
turn off, and turning it off takes the alarm with it.

`weekly.timer.example` runs the sweep on Mondays. The timer is what makes the status line able to
alarm at all — it reports the age of the last sweep, so **a timer that dies becomes visible at the
next session start** instead of looking exactly like a store with nothing wrong in it.
