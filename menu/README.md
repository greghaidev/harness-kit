# The menu — twelve capabilities, none installed by default

Specified during design review, 2026-08-20. Each spec is written to be built cold, without
re-deriving the reasoning.

**Nothing here is installed by the bootstrap.** That is deliberate: the binding constraint is
your time, not build hours, and a thirty-hour component that never gets finished is worth
zero. The menu holds well over 150 hours of proposals. Pick one, build it, use it for a fortnight, then pick the next.

Convictions below are the reviewer's, 0–3. ★ marks the three worth building first.

---

## ★ claim-spine — claims as first-class objects
*Highest conviction in the review. Everything in `defense`, `audience` and
`provenance-exhibit` hangs off this.*

A note type `claim`, stored in the existing memory store. One assertion per note:

```yaml
statement:            the assertion, in one sentence
source:               query name | file path | document + locator
as_of:                the date the supporting data was current
scope:                what population/period it covers
assumptions:          [ ... ]
does_not_establish:   the tempting conclusion this does NOT support
rests_on:             [claim-id | figure-id]      # the dependency edge
venue:                where it was stated, or "unpublished"
superseded_by:        claim-id                     # retraction, never deletion
```

Two operations carry the value:

- **Reverse dependency query.** When a source number moves, ask *which arguments just broke*.
  This is blast radius for an argument rather than for a schema.
- **Supersession as retraction.** A claim is never edited or deleted, only superseded, chain
  preserved. "Do you still stand by that figure" becomes answerable.

The session digest gains one section: claims on the record whose supporting evidence has been
superseded since the claim was made.

**Capture is a CLI, not a press hook.** Deliverables are mixed — decks for executives,
documents for peers, notebooks for analysis — so a build-time hook would capture the notebook
third and silently miss the rest. That is the failure mode that empties registries. The
`claim` verb already installed by the core is the capture surface; this component gives it a
real schema and a query layer.

**Cost** ~15–20h. **Falsifier:** you never query the reverse-dependency direction, and
figures turn out to be one-off rather than reused across communications.

---

## ★ provenance-exhibit — render the trace, do not merely check it
*Conviction 3. The least obvious idea in the review.*

The press today treats traceability as a build-time pass/fail: private, pre-publication, and
it emits nothing. Invert it. On every successful build, **render the trace**:

- each figure carries a compact provenance line — source, derivation name, as-of date
- the document closes with a derivation appendix keyed by figure, regenerated every build so
  it cannot drift from the figures it defends

> Credibility is adjudicated in the room, **after** publication. A build-time gate treats
> traceability as an error condition, because a builder needs to catch a bad number before
> merge. What an analyst needs is the trace as an **exhibit** after release. That is the
> distinction between a check and a defense.

**Cost** 10–14h *if* the claim gate can emit its evidence rather than only pass/fail it —
roughly double if not. **Falsifier:** across several defended deliverables you never reach
for the appendix in a live challenge.

---

## ★ blast-radius — downstream consumers of any table, field or view
*Conviction 3. The highest-value single item for a data architect.*

Given a table, field or view, return **every downstream consumer** — report, ETL job,
dashboard, dependent application — as an enumerated, source-cited dependency list. Built from
static SQL/DDL parsing plus co-change analysis over repo history; no runtime instrumentation,
no warehouse write access.

This converts "I think this breaks something" into a defensible artifact, which is exactly
the shape of a migration conversation.

**Cost** ~20h+, and it is the one most sensitive to your warehouse's specifics. **Falsifier:**
lineage turns out to be already documented and current in an existing catalog tool.

---

## objection-docket — the challenges actually received
*Conviction 3.*

A durable, append-only record of challenges **received**, not anticipated:

```yaml
challenge:      the challenge in its original wording
claim:          which claim it hit
evidence_used:  what was cited in response
outcome:        answered | bounded | could_not_establish
response:       what was actually said
revisions:      [ ... ]        # later revisions never erase the earlier response
```

Explicitly **not** passive closure: an objection can stop needing follow-up while remaining
part of the position's history. Before a communication, the harness renders a packet —
anticipated objections, established answers, known boundaries, and the questions that cannot
be answered from available facts.

**Cost** 8–12h. **Falsifier:** captured objections are never consulted when preparing later
defenses.

---

## dissent-file — the per-deliverable opposition file
*Conviction 2. The only option that measures its own falsifier.*

Each deliverable gets a companion note, never rendered into the deliverable: the strongest
expected challenges written **at build time**, the prepared response and evidence for each,
and — appended after the venue — the challenges **actually raised** and which responses held.

Because it records both halves, the file itself answers whether rehearsal works:

> "The expected-challenge lists repeatedly fail to predict the challenges actually raised.
> If rehearsal never anticipates the room, it is ceremony."

**Cost** 6–8h. **Falsifier:** built in, as above. Secondary: you stop writing them after the
second deliverable.

---

## definition-registry — a compiler for prose
*Conviction 2.*

A note type for governed terms — the metrics and entities whose definitions you own. One
registered definition per term, with revision history. At build time the press flags
deliverable usage inconsistent with the current definition, and flags figures computed under
a definition that has since been revised. A deliberate revision triggers a digest listing of
every claim resting on the prior one.

> "Builders get this discipline from type systems; prose has no compiler, so the harness must
> supply one. Being caught using one term two ways in two deliverables is the specific way
> an analyst loses a room."

Scope v1 to exact-term matching plus definition-bound figures. Inferring "inconsistent usage"
loosely is where the cost explodes.

**Cost** 8–12h. **Falsifier:** zero true positives across three or four deliverables.

---

## question-desk — codenames pointed at questions, not sessions
*Conviction 2. This replaces the sprint cadence the kit deliberately omits.*

Apply the deterministic-codename mechanism one level up. Each inbound question gets a stable
handle, the asker, **the asker's deadline, not your estimate** — a state
(open/drafted/delivered/superseded), and links to the claims and figures used to answer it.
The digest orders open questions by deadline. A question recurring in new wording from a new
asker surfaces every prior answer and the evidence state at the time.

> "A builder's inbound work arrives as tickets with estimates **they** control; a
> communicator's arrives as questions pinned to **other people's** calendars. The obvious
> design finds past *sessions*; the real retrieval problem is finding past *answers*."

**Cost** 6–10h. **Falsifier:** fewer than ~2 substantive questions a week, or no question ever
reopened by a later one within 90 days.

---

## parity-checker — did the audience variant change the claim?
*Conviction 2. Directly serves the mixed-deliverable reality.*

A semantic diff across renderings of the same position that flags **broadened or narrowed
claims, removed conditions, dropped as-of language, unsupported certainty**, and claims
present in one variant but absent from another. Factual parity, not prose similarity — an
ordinary text diff cannot tell harmless rewording from a material change in what is asserted.

Operates over the claim spine where installed, or against a designated reference document
where not.

**Cost** 12–18h. **Falsifier:** manual review shows consequential meaning changes are no more
detectable with it than without, while most findings concern harmless wording.

---

## altitude-splitter — one spine, three renderings
*Conviction 1–2.*

One gated claim set → executive one-pager, peer brief, technical appendix. Each artifact
carries only the claims licensed for that altitude; the spine stays one object so a correction
propagates. **Build fails if any altitude introduces a claim absent from the spine.**

**Cost** 14–20h. **Falsifier:** audiences turn out homogeneous enough that one format serves.

---

## live-query — settle it in the room
*Conviction 2.*

Natural-language querying over live schemas and cached snapshots returning **source-cited**
answers fast enough to settle a factual dispute during a conversation, rather than "let me get
back to you." Precomputed summaries cached for sub-few-second response.

The most direct hit on "information available quickly", and the one that most changes how a
meeting goes.

**Cost** 15–25h, heavily dependent on your warehouse access latency. **Falsifier:** disputes
in the room turn out to need judgment rather than facts.

---

## stakeholder-cards — the join is the value
*Conviction 2.*

Lightweight per-audience cards: role, last exchange, the vocabulary they use, claims they have
accepted or rejected, open questions they own. Injected into the digest when a session names a
person or forum. The value is not the card, it is the **join** — claim × stakeholder × stated
position → conflict alert ("this contradicts what they committed to in March").

**Cost** 10–14h. **Falsifier:** fewer than about five recurring decision-makers, in which case
you hold it in working memory.

---

## archaeology — why does this exist
*Conviction 2.*

Reconstruct the intent and lineage of code you did not write, narrating **from the commit and
diff stream only**: each field and table gets a birth commit and a change chain, and the tool
**refuses to assert intent not traceable to a diff**. That refusal is the whole point — it is
the difference between reconstruction and plausible invention.

**Cost** 12–20h. **Falsifier:** repo history is too shallow or too squashed to carry intent.

---

## Also specified, lower conviction, cheap

- **source-capsules** (conviction 2) — a portable evidence capsule per cited fact: exact excerpt,
  locator, surrounding context, retrieval date, fingerprint; for computed facts the named
  derivation and its snapshot.
- **precedent-bank** (conviction 2) — prior architectural decisions with context, rationale, outcome
  and reversibility, queryable by similarity. Answers "we tried this in 2019 and it failed."
- **position-change-record** (conviction 2) — a semantic diff against the previously *communicated*
  version, requiring an explanation for each material change.
- **contested-fact-lane** (conviction 1) — park contradictory evidence; a session cannot end with
  unresolved contradictions in it.
- **inbound-intake** (conviction 1) — ingest others' assertions (meeting notes, partner decks) with
  provenance and an explicit admission step, "without laundering them into unearned authority."
