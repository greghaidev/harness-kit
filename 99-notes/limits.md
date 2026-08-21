# What this kit does not do

An honest accounting of the limits. Read this before trusting any part of the kit on a
high-stakes claim.

Most documentation describes what a tool does. The failures below are the ones that matter,
because each is a place where the harness will feel like it is protecting you and will not be.

## 1. There is no independent reviewer — the big one

Everything reviewing your work here is Claude, including whatever reviews the work of the agent
that produced it. That matters more than it sounds like it should.

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
Claudes closes that gap. On a high-stakes claim the residual risk sits with you, and the
harness should say so out loud rather than offer assurance it does not have.

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

## 3. There is no priority taxonomy, and you should not import one

Ranking schemes that sort work by severity class assume you control the queue. Here, priority is
*which room fires next*. The lane mechanism is available from the menu; any fixed class order
you bolt onto it will be overridden by your calendar within a week, at which point the ordering
is decoration that still costs you the effort of maintaining it.

## 4. There is no autonomy dial

Some harnesses carry a tier system deciding what ships without the operator looking at it. That
only makes sense where the operator owns the repository and is the one who merges.

You are one reviewer among several and merge nothing unilaterally, so a dial encoding
merge authority would encode an authority you do not have.

What survives is the underlying judgment, and it is now a different question. Not *what ships
without me*, but **what have I verified well enough to be worth a teammate's review**. That is
the standard the operating agreement asks you to hold, and it is not mechanised — it is yours.

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
