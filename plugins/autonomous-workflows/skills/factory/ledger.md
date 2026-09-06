# The ledger

One file at `%TEMP%\factory\<feature-slug>\factory.md`, deliberately outside the
repo and outside every worktree, because worktrees get deleted mid run. It is
the run's memory: the orchestrator's context is disposable, this file is not.
Only the orchestrator writes it. Everyone else reports their lines upward.

Write it **before** reporting anything to the user. A session that dies mid run
resumes from here and redoes nothing.

## Template

```markdown
# Factory run: <feature name>

- Started: <YYYY-MM-DD HH:MM:SS>
- Repo: <path> @ <remote>
- Linear project: <name> (<id>)
- Parent issue: <ELI-nnn>
- PRD: <ELI-nnn>
- Stack: <language>, <package manager>, test `<cmd>`, typecheck `<cmd>`, lint `<cmd>`
- Lanes: <lane>, <lane>
- Integration branch: factory/<feature-slug>
- Parked marker: <the existing Linear state agreed in Phase 0, or "ledger only">
- Totals: builders <n>, subagents <n>, live now <n>/10, queued <n>

## Tickets

| # | Linear | Title | Lane | Blocked by | Status | Branch | PR |
|---|--------|-------|------|-----------|--------|--------|-----|
| 1 | ELI-41 | Seed schema | data | none | Done | factory/eli-41-seed-schema | #12 |
| 2 | ELI-42 | List view | ui | 1 | In progress | factory/eli-42-list-view | #13 |
| 3 | ELI-43 | Export CSV | api | 1 | Parked (Q2) | | |

Status is one of: Todo, In progress, Adversary, Fixing, Merging, Done, Parked (Qn), Failed.

## Parked questions

### Q1: <one line question> [ANSWERED]

- Blocks: ELI-43, ELI-47
- What is unresolved: <two sentences, no more>
- Options: <a> / <b> / <c>
- Recommendation: <one of them, and why in one line>
- Answer: <what the user said, verbatim>, <date>

### Q2: <one line question> [OPEN]

...

## Decisions log

One line each. Decisions the builders took without asking, so they are auditable
and so the next run does not relitigate them.

- ELI-41: table named `run_event`, singular, matches CONTEXT.md glossary
- ELI-42: virtualised the list at 200 rows rather than paginating

## Dispatch log

One line per ticket, written when it is dispatched and updated when it lands.
Tickets held back by the ten agent cap get a queued line, so a stalled run shows
whether it is waiting on work or waiting on slots. The header totals move with
it, and the run stops for a check in once agents spawned passes four per ticket.

- 09:14 ELI-41 dispatched (2 subagents). Green, 14 passed 0 failed, adversary clean, merged #12. Slot returned 09:41.
- 09:41 ELI-42 dispatched. Green, adversary found 1, fixed, merged #13.
- 09:41 ELI-43 dispatched. Parked as Q2 before any code.
- 09:52 ELI-44 queued, cap full at 10 live. Dispatched 10:03 on ELI-42's slot.
```

## Parking rules

The difference between "finish as much as possible" and "stop constantly" lives
here. Get it wrong in either direction and the loop is useless.

**Decide and log.** Anything reversible in an afternoon by someone who can read
the code:

- Names, file layout, module boundaries within a lane
- Which already chosen library to reach for, and how to call it
- Test structure, fixture shape, how many cases
- Spacing, copy, empty state wording, icon choice
- Anything the PRD or an ADR already answers, even indirectly

**Park.** Anything where guessing wrong means throwing away work, or means
shipping something the user did not agree to:

- What a feature should do when the PRD is genuinely silent, not merely terse
- Money, auth, permissions, or the data model, where the PRD does not settle it
- Anything needing a credential, an account, a paid tier, or an external approval
- Anything irreversible: a public API shape, a destructive migration, a name
  users will see and depend on
- A conflict between the ticket and an ADR
- An adversary finding that survives two passes

**The test.** If you can write the question as "should it be A or B", and picking
wrong costs an hour, decide. If picking wrong costs a day or costs the user's
trust, park.

Parking a ticket stops **that ticket only**. Everything not downstream of it keeps
running. The ticket keeps whatever Linear state it is in, and gets the parked
comment from [closing.md](./closing.md) so the reason is visible outside this
session. Use a different state only if Phase 0 agreed on an existing one. Never
create a state or label for it.
