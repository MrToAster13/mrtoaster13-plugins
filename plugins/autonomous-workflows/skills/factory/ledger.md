# The ledger

One file at `<ledger-root>/factory/<feature-slug>/factory.md`, deliberately outside the
repo and outside every worktree, because worktrees get deleted mid run. It is
the run's memory: the orchestrator's context is disposable, this file is not.
Only the orchestrator writes it. Everyone else reports their lines upward.
`<ledger-root>` is `%TEMP%` on Windows and `~/.local/state` on Linux and macOS,
where `/tmp` is often a tmpfs that a reboot wipes.

Write it **before** reporting anything to the user. A session that dies mid run
resumes from here and redoes nothing.

**Write it with the editing tools, not with the shell.** Under Claude Code's auto
mode, a Bash write to a path in `%TEMP%` or another directory outside the project is refused by the permission classifier
as an external system write, so `sed -i`, a heredoc, or a `>>` redirect against
this file comes back denied every time. Reads through the shell are fine. This is
the one place in the run where the usual preference for shell commands is wrong,
and each run rediscovers it the hard way otherwise.

## Template

```markdown
# Factory run: <feature name>

- Started: <YYYY-MM-DD HH:MM:SS>
- Repo: <path> @ <remote>
- Ticket project: <name>
- Parent issue: <KEY-nnn>
- PRD: <KEY-nnn>
- Stack: <language>, <package manager>, test `<cmd>`, typecheck `<cmd>`, lint `<cmd>`
- Lanes: <lane>, <lane>
- Integration branch: factory/<feature-slug>
- Blocker tickets: filed in <project>, linked as blocks on the stopped ticket
- Totals: builders <n>, subagents <n>, resolvers <n>, adversaries <n>, integrators <n>, live now <n>/10, queued <n>

## Tickets

| # | Ticket | Title | Lane | Blocked by | Status | Branch | PR |
|---|--------|-------|------|-----------|--------|--------|-----|
| 1 | ABC-41 | Seed schema | data | none | Done | factory/abc-41-seed-schema | #12 |
| 2 | ABC-42 | List view | ui | 1 | In progress | factory/abc-42-list-view | #13 |
| 3 | ABC-43 | Export CSV | api | 1 | Blocked (ABC-88) | | |

Status is one of: Todo, In progress, Adversary, Fixing, Merging, Done, Blocked (<KEY>-nnn), Failed.

## Blocked tickets

### ABC-43, blocked by ABC-88 [OPEN]

- Attempts (in order): <one line each, what was tried and what came back. The
  last line is the resolver's UNRESOLVED report, verbatim.>
- Answer: <what the user said, verbatim>, <date>

The question, the options and the recommendation live in the blocker ticket, not
here. Resume reads live ticket state, so one copy is enough.

## Decisions log

One line each. Decisions the builders took without asking, so they are traceable
and so the next run does not relitigate them.

- ABC-41: table named `run_event`, singular, matches CONTEXT.md glossary
- ABC-42: virtualised the list at 200 rows rather than paginating

## Dispatch log

One line per ticket, written when it is dispatched and updated when it lands.
**Record the model every agent ran on**, and every escalation with its reason.
It must match the ladder in [SKILL.md](./SKILL.md), so the line is a paper
trail: a missing model, a model off the ladder, or any Fable or Sonnet entry, is the only way a model violation stays visible after the agent's
context is gone.
Tickets held back by the ten agent cap get a queued line, so a stalled run shows
whether it is waiting on work or waiting on slots. The header totals move with
it, and the run stops for a check in at the ceiling set in [SKILL.md](./SKILL.md).

- 09:14 ABC-41 dispatched (opus, 2 subagents on haiku). Green, 14 passed 0 failed, adversary clean, merged #12. Slot returned 09:41.
- 09:41 ABC-42 dispatched (opus). Green, adversary found 1, fixed, merged #13.
- 09:41 ABC-43 dispatched (opus). Blocked by ABC-88 before any code.
- 09:52 ABC-44 queued, cap full at 10 live. Dispatched 10:03 (opus) on ABC-42's slot.
```

## Blocking rules

A ticket may only stop when a human decision is genuinely required. "The
orchestrator did not want to dig" is not a reason, and the rules below make that
outcome illegal rather than merely discouraged.

**Decide and log.** Anything reversible in an afternoon by someone who can read
the code:

- Names, file layout, module boundaries within a lane
- Which already chosen library to reach for, and how to call it
- Test structure, fixture shape, how many cases
- Spacing, copy, empty state wording, icon choice
- Anything the PRD or an ADR already answers, even indirectly

**Escalate.** Everything else, and above all anything where guessing wrong means
throwing away work, or means shipping something the user did not agree to:

- What a feature should do when the PRD is genuinely silent, not merely terse
- Money, auth, permissions, or the data model, where the PRD does not settle it
- Anything needing a credential, an account, a paid tier, or an external approval
- Anything irreversible: a public API shape, a destructive migration, a name
  users will see and depend on
- A conflict between the ticket and an ADR
- An adversary finding that survives two passes

Escalation is not stopping: it is the ladder in
[SKILL.md](./SKILL.md), and most questions die on it. A missing credential, an
unstated price, an approval from someone who is not the user: those come back
UNRESOLVED from rung 1 quickly, which is the ladder working, not the ladder
wasted.

**The evidence rule.** A ticket is not blocked until the ledger holds the attempt
log, ending in the resolver's own UNRESOLVED report quoted verbatim (or, for a
resolver the harness ended before it reported, its last output or, if it
returned none, the error the harness gave). An
orchestrator that writes `Blocked` without it has broken the skill. The log is
copied into the blocker ticket body, so the user sees the effort rather than
taking it on faith.

**The test.** If you can write the question as "should it be A or B", and picking
wrong costs an hour, decide. If picking wrong costs a day or costs the user's
trust, spend the ladder. The ladder comes back empty, and only then: file the ticket.

Blocking a ticket stops **that ticket only**. Everything not downstream keeps
running. The blocker ticket is a new ticket in the same project, linked as
a blocker of the stopped ticket, carrying the question, the options, the
recommendation, and the attempt log. The stopped ticket gets the blocked comment
from [closing.md](./closing.md) and keeps its current state. Never create a state
or label for this.
