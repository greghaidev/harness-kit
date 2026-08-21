# INSTALL — paste this into a fresh Claude Code session at work

You are installing an agent harness for a **data architect**. Read this whole file before
acting. It is a script for you to execute, not documentation for a human to follow.

The operator you are installing for communicates far more than they build. They analyse
systems they did not write, explain data migrations to business users who need to understand
both the data changes and the technology changes, and defend positions with established facts.
Every design decision below follows from that and from four hard constraints.

## The four constraints — violate none of them

1. **The work repo is shared and PR-reviewed.** Install NOTHING into it. Everything goes in
   `~/.claude/` and `~/harness/`. No teammate should ever have to agree to any of this.
   The one exception is `git config core.hooksPath`, which lives in `.git/config` and is
   **not** shared — that is a per-clone local setting, so it is safe.
2. **Claude only.** No second model lineage is available, ever. Nothing here may depend on
   one. Where a second model lineage would normally review, this uses a **held-out
   context** instead — a different evidence position, not a different lineage. That is
   weaker in a specific way, and `99-notes/limits.md` says exactly how. Do not paper over it.
3. **No production, no deployment, no customer surface.** They ship analysis and explanation.
   Anything about deploys, staging, or uptime does not apply and must not be installed.
4. **Their time is the binding constraint, not build hours.** A component never finished is
   worth zero. Install the core. Do not install menu items they have not asked for.

## Phase 0 — Interview. Ask these before touching anything.

Ask all of them in one message, as a numbered list. Do not guess; the answers change what
you write into their operating agreement, and no template can know them.

1. Where does your work live — repo paths, warehouse/database names, the systems you are
   most often asked about?
2. What are your deliverables actually called at your company, and where do they land?
   (Names matter: the fact-check hook triggers on path and filename patterns.)
3. Who reviews your work, and what makes something worth their time versus something you
   should have settled yourself?
4. Roughly how many substantive questions do you field per week, and how often does a
   position you took get challenged later?
5. What are your governed terms — the metrics and entities whose definition you own and
   whose drift would embarrass you?
6. Is there anything you are NOT allowed to put in a local file? (Regulated data, customer
   PII, anything under legal hold.)

Answer 6 is a hard gate. If any category is restricted, say so plainly and exclude it from
the store before installing anything.

## Phase 1 — Install the core

The core is not optional and is not a menu item: it is the substrate the rest needs. Install
all four.

```bash
mkdir -p ~/harness ~/harness/store/work ~/harness/store/meta
cp -r <this-kit>/core ~/harness/core
cp -r <this-kit>/menu ~/harness/menu          # specs only; nothing here is active yet
python3 -m venv ~/harness/.venv
~/harness/.venv/bin/pip install -q pyyaml "mcp[cli]"
```

**`core/01-memory` — the store.** Notes are markdown files with YAML frontmatter, in a git
repo, written only through an MCP server. Every write is committed. Two tenants: `work` and
`meta` — both names are arbitrary labels; repoint the roots with env rather than editing code:

```bash
export HARNESS_WORK_STORE=~/harness/store/work      # project + analysis notes
export HARNESS_META_STORE=~/harness/store/meta  # notes about the tooling itself
git -C ~/harness/store/work init -q && git -C ~/harness/store/meta init -q
~/harness/.venv/bin/python ~/harness/core/01-memory/store_test.py   # must PASS
```

Register the server user-scoped, so it is available in every repo and committed to none:

```bash
claude mcp add --scope user harness-memory -- \
  ~/harness/.venv/bin/python ~/harness/core/01-memory/memory_server.py
```

**`core/02-session` — identity and terminal state.** A deterministic codename per session so
a later session can find this one; a session index; and the Stop guard, **retargeted** (see
Phase 2 — this is the one behaviour change that matters).

**`core/03-press` — the document engine.** Markdown in, a finished magazine-format document
out, with a claim gate that fails the build when a stated fact stops tracing. Run its tests:
`~/harness/.venv/bin/python -m pytest ~/harness/core/03-press/test_press_kit.py -q`.

**`core/00-agreement` — the operating agreement.** Copy `CLAUDE.md.template` to
`~/.claude/CLAUDE.md` and fill every `<<ANGLE BRACKET>>` from the Phase 0 answers. Leave no
placeholder behind; an unfilled placeholder is worse than an absent section, because it reads
as a rule while meaning nothing.

## Phase 2 — The retarget. This is the point of the whole install.

The obvious design refuses to let a session end with **dangling work**: a queued item, or
an uncommitted change. That is the correct invariant for someone who builds, whose residue
risk is a dropped task.

It is the wrong *state* for someone who analyses. Consider the session that actually happens
here: they ask a question, you run a query, you reach a conclusion, they paste the number
into someone else's deck. Queue empty. Worktree clean. The session ends **clean** — and its only
durable product leaves with no provenance. Months later the figure is quoted back at them and
the harness that watched that session close holds nothing.

That is not a missing feature. It is a correct-shaped invariant pointed at the wrong state.

So the trigger widens from *did this session change something* to *did this session produce
something a stakeholder could quote back*, and the terminal vocabulary grows from three to
six:

```
claim "<conclusion>" --source "<what it traces to>" [--does-not-establish "<...>"]
limit "<what you could not establish>"     # a bounded "I do not know" — legitimate
discard "<why>"                            # reached something not worth keeping
add "<next piece of work>"                 # more to do -> then DO it
hold "<what you need decided>"             # someone else's call
clear                                      # nothing produced worth recording
```

`--does-not-establish` records the tempting conclusion a claim does **not** support. It is
one field and it is the sharpest idea in the kit: it is what stops a correct number being
used to argue something it cannot carry.

`limit` exists because an analyst must sometimes end on an explicitly bounded *I do not know*.
Turning every unresolved limit into queued work distorts the record rather than improving it.

Wire the Stop hook into `~/.claude/settings.json` (user scope, never the repo):

```json
{
  "env": { "HARNESS_HOME": "~/harness",
           "HARNESS_WORK_STORE": "~/harness/store/work",
           "HARNESS_META_STORE": "~/harness/store/meta" },
  "hooks": {
    "SessionStart": [{ "hooks": [
      { "type": "command",
        "command": "~/harness/.venv/bin/python ~/harness/core/01-memory/session_context.py" }
    ]}],
    "Stop": [{ "hooks": [
      { "type": "command",
        "command": "~/harness/core/02-session/unfinished-work-stop-guard.sh" }
    ]}]
  }
}
```

If a `settings.json` already exists, MERGE — read it, add these keys, write it back. Never
overwrite one.

## Phase 3 — Verify, and report honestly

```bash
~/harness/.venv/bin/python ~/harness/verify.py
```

It checks each component and prints a pass table. Report the table verbatim. **Do not
describe a component as installed if its check did not pass** — an install that overstates
itself is worse than one that fails loudly, because the operator will build on it.

Then prove the loop end to end, in front of the operator, rather than asserting it works:

1. `memory_put` a note, `memory_search` for it, `memory_get` it back.
2. Record a claim, then let the session try to stop, and show that it is allowed to.
3. Run a query-shaped command with no declaration, try to stop, and show that it is blocked.
4. Build `~/harness/core/03-press/` sample markdown into a PDF and open it.

## Phase 4 — Stop. Do not install the menu.

`menu/` holds twelve capabilities specified during design review. Every one is real and every
one is deferred deliberately: a minimal default and a large menu, because a thirty-hour
component that never gets finished is worth nothing.

Tell the operator what is in the menu, in one line each. Install nothing unless they name it.

The three rated highest, if asked:

- **claim-spine** — claims as first-class objects with dependency edges, so when a source
  number moves you can ask which arguments just broke.
- **provenance-exhibit** — render the derivation appendix into the document, so the trace is
  an exhibit *after* publication rather than a check before it.
- **blast-radius** — every downstream consumer of any table, field or view, as an enumerated
  source-cited list rather than a hunch.

## What this kit deliberately does NOT install

- **No sprint workflow.** Removed unanimously on review:
  velocity is meaningless when the output is a defended position, the calendar is owned by
  other people, and sprint boundaries force premature closure before contradictions resolve.
- **No locked priority classes.** The mechanism can come back from the menu; the inherited
  build taxonomy does not, because priority here is *which room fires next*.
- **No multi-model board, no cross-lineage fact-check panel.** Constraint 2. What replaces
  them is in `05-factcheck` in the menu, and it is honestly weaker.
- **No tier dial.** It encodes *what ships without the operator*, and the operator is not the
  merger here — their reviewers are.
