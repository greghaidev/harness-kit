# harness-kit

An agent harness for a **data architect**: someone who communicates more than they build,
analyses systems they did not write, explains data migrations to business users, and defends
positions with established facts.

Built for one person working alone inside a shared codebase, and **shaped for analysis rather
than construction**. The
original makes the *session* durable and the *task* the unit of work. That is correct for a
builder, whose residue risk is a dropped task. It is wrong here, where the residue risk is an
**unrecorded conclusion** — a number that leaves a session with no provenance and returns six
months later in somebody else's slide.

## Install

Open a fresh Claude Code session at work and paste `INSTALL.md`. It interviews you for the
things no template can know, installs the core, wires one hook, and then runs `verify.py`,
which proves each component on the machine it landed on rather than asserting it from config.

Everything lands in `~/.claude/` and `~/harness/`. **Nothing goes in your team's repo.**

## What gets installed

| | |
|---|---|
| `core/00-agreement` | the operating agreement — claim-as-primary-object, evidence discipline, the honest limits |
| `core/01-memory` | durable memory + capability registry over MCP; notes are markdown in a git repo, every write committed |
| `core/02-session` | session identity, a session index, and the Stop guard **retargeted** at conclusions |
| `core/03-press` | markdown → magazine-format document → PDF, with a claim gate that fails the build when a stated fact stops tracing |
| `core/04-journal` | decisions, friction and ideas in one command; the day rolls itself up on every stop |

## The one behaviour change that matters

A session where you analyse data, reach a conclusion, and paste the number into someone
else's deck has an empty queue and a clean worktree. It ends **clean**. Its only durable
product walks out with no provenance.

That is a correct-shaped invariant — *no session ends with dangling state* — pointed at the
wrong state. So the trigger widens from *did this change something* to *did this produce
something a stakeholder could quote back*, and the terminal vocabulary grows:

```
claim "<conclusion>" --source "<what it traces to>" [--does-not-establish "<...>"]
limit "<what you could not establish>"      # a bounded "I do not know" — a real ending
discard "<why>"                             # reached something not worth keeping
add / hold / clear                          # as before
```

## The menu

`menu/README.md` specifies twelve further capabilities — a claim spine with reverse
dependency edges, provenance rendered as an exhibit, a downstream blast-radius graph, an
objection docket, a definition registry, and more. **None is installed by default**, by
deliberate decision: the binding constraint is your time, and a thirty-hour component you
never finish is worth zero. Build one, use it a fortnight, then choose the next.

## What this cannot do

`99-notes/limits.md`. Read it before trusting anything here on a high-stakes
claim. The short version: **there is no second model lineage at work**, so correlated error —
a framing both the drafter and the reviewer find natural — passes uncaught. Held-out reviewers
narrow that gap. They do not close it.
