# What this kit does not do

An honest accounting of the limits. Read this before trusting any part of the kit on a
high-stakes claim.

Most documentation describes what a tool does. The failures below are the ones that matter,
because each is a place where the harness will feel like it is protecting you and will not be.

## 1. The independent reviewer is available now, but nothing here uses it yet

**Amended.** This section was written on the constraint that Claude was the only model lineage
available at work. That is no longer true — open-weight models are reachable through Devin. The
limit below is therefore no longer a fact about the environment; it is a fact about **this kit**,
which still reviews Claude's work with Claude.

The fix is not in this repository. It is `claim-ledger`, which runs a per-claim ledger past
reviewers from lineages *other* than the one that drafted the artifact, and which is the correct
home for that job. Until this kit calls out to it, everything in this section still applies to
everything here.

Everything reviewing your work *in this kit* is Claude, including whatever reviews the work of
the agent that produced it. That matters more than it sounds like it should.

A model asked to check its own output gives you a **correlated** second opinion, not an
independent one. It shares its blind spots with itself: the framing it found natural when
drafting, it finds natural when reviewing.

**What this kit does instead:** held-out-context reviewers. Agents that see the artifact and
the primary sources and **never the reasoning that produced them**. They cannot inherit the
drafter's rationalisation because they never saw it — a genuinely different evidence position.
Give each a distinct lens (arithmetic reproduction · source fidelity · does-the-claim-overreach ·
what-is-missing) rather than running several identical ones. Lens diversity is the only
decorrelation lever available.

**What that does NOT catch, stated plainly:** a framing error that both the drafter and the
reviewer find natural, and a plausible-but-wrong derivation both accept. No arrangement of
Claudes closes that gap — and now that a second lineage is genuinely reachable, "no arrangement
of Claudes" has stopped being an excuse and become a to-do. On a high-stakes claim, run the
artifact through `claim-ledger` with a non-Claude roster. The residual risk still sits with you,
but it no longer has to sit there alone.

## 2. There is no sprint machinery, deliberately

An earlier design included a sprint loop — timeboxed cycles with velocity as a signal. It was
removed on review, unanimously, as *actively harmful* for this kind of work rather than merely
unnecessary.

Velocity is a meaningless signal when the output is a defended position. The calendar belongs
to other people: work arrives as questions pinned to somebody else's meeting, not as tickets
with estimates you control. And a timebox forces premature closure of a position before its
contradictions resolve, which is the one failure an analyst cannot afford.

> "A memory system that is partly ignored teaches its operator to ignore the rest of it."

The deadline ordering this work actually needs is `menu/question-desk`, which orders by the
**asker's** deadline rather than your estimate.

## 3. The lanes are in core now, and the argument against a class order still stands

Ranking schemes that sort work by severity class assume you control the queue. Here, priority is
*which room fires next*. Any fixed class order you bolt onto the lanes will be overridden by your
calendar within a week, at which point the ordering is decoration that still costs you the effort
of maintaining it.

This section originally said the lane mechanism itself was a menu item. It is now `core/05-lanes`,
promoted because the coordination half turned out to be load-bearing on its own: with more than
one agent session open, nothing else stops two of them editing the same files. That is a mechanical
guard against a mechanical failure, and it does not depend on the ordering question at all.

The argument above survives intact and is enforced in the code rather than left to discipline.
`lanes priorities` renders a register you stack **by hand**, and its class order produces
**advisory badges only**. Nothing dispatches from it: `lanes pick` orders by lane rank and then
by arrival, and has no access to the taxonomy. If you stop stacking the register it goes stale
and says so, and the board keeps working without it. That is the shape a ranking scheme has to
take here — something you can abandon without breaking anything.

## 4. There is no autonomy dial

Some harnesses carry a tier system deciding what ships without the operator looking at it. That
only makes sense where the operator owns the repository and is the one who merges.

You are one reviewer among several and merge nothing unilaterally, so a dial encoding
merge authority would encode an authority you do not have.

What survives is the underlying judgment, and it is now a different question. Not *what ships
without me*, but **what have I verified well enough to be worth a teammate's review**. That is
the standard the operating agreement asks you to hold, and it is not mechanised — it is yours.

`core/05-lanes` carries a `tier` field and an `agent_auto_approve_tiers` setting, and they are
not that dial. They gate whether an agent-filed item needs your yes before anyone **starts** it.
Nothing in this kit can merge anything, so nothing in this kit can decide what ships. If you ever
find yourself reading a tier as merge authority, that is the confusion this section exists to
prevent.

## 5. There is nothing about deployment

No staging, no promotion, no deploy scripts, no uptime monitoring, no release gates. This kit
assumes you ship analysis and explanation, not running software. If that changes, none of this
covers it.

## 6. There is no front-end or visual review

No browser automation, no accessibility gate, no screenshot comparison. If you start producing
interfaces, this kit has nothing to say about them.

## 7. The claim gate proves less than it appears to

It proves a file exists, a count is current, a quoted function still says what is quoted. It
proves **nothing about interpretation**. A document can pass the gate completely and still argue
something the data does not support. The gate is a floor, not a ceiling, and treating a green
build as a fact-check is the specific way it will mislead you.

## 8. The fact-check layer is honest about being partial

The intake gate, the per-claim ledger and the fail-closed aggregation all work as designed. The
independence property does not — see item 1. Run it, read the ledger, and keep your own
judgment in the loop on anything that matters.

## 9. The lane board coordinates your agents, not your colleagues

The collision check compares the surfaces **your own sessions declare** against each other. It
knows nothing about what anybody else is editing right now, and it never will — there is no feed
of that. Two of your sessions cannot collide silently; you and a teammate still can, exactly as
before.

Three more boundaries worth knowing before you rely on it:

- **A lease is a hint, not a mutex.** Locks are advisory notes, and `--force` is one flag away.
  Two agents that both force through will both proceed, and the board will faithfully record that
  they did. It stops the accident, not the intent.
- **A declared surface is only as good as the declaration.** An agent that claims `docs/` and then
  edits the warehouse models has defeated the check completely, and nothing detects that.
- **"Shipped" needs a repo to be answerable.** Without `repo` in `lanes.json`, `reconcile` can
  close nothing and says so on every run. It fails visibly rather than quietly, which is the most
  the design can do — but a board nobody configured is a board that never closes anything.

---

## Two things that work better than you would expect

**Git hooks install personally.** `git config core.hooksPath` lives in `.git/config`, which is
per-clone and **not** shared. So a pre-commit guard and a post-commit backup mirror can be
installed against a shared repository without touching the repository, without a pull request,
and without changing anybody else's behaviour.

**The memory store is genuinely portable.** Notes are markdown with YAML frontmatter in a git
repository; the store is standard library plus one YAML package, the server is a single file,
and the tenant roots are environment variables. Its 63-check boundary suite passes on a fresh
machine with no adaptation. That is why it is installed first and why everything else assumes
it.
