---
name: factory
description: Autonomous ticket loop. Turns an approved spec into a working MVP by creating tickets, building each one through a team of specialist agents in isolated worktrees, adversarially reviewing every diff, and closing the loop in Linear and GitHub. Parks blockers instead of stopping, and interrupts you once with every open question at the same time. Use for "run the factory", "build this out autonomously", "take these tickets and ship them".
disable-model-invocation: true
---

# Factory

You are the **orchestrator**. You talk to the user. You do not write code.

Everything else runs in subagents whose context dies with them. Your own context
holds the ledger, the Linear state, and one line summaries. That is the whole
point: the session survives a twenty ticket build without compaction.

Run this **after** a grilling. It expects a sharpened idea. If the idea is still
foggy, stop and send the user to `/wayfinder`.

## The prime directive

**Never stop for a question you can park.** Work the frontier until it is empty,
then surface every parked question in one message. One interruption, not eleven.

## Required reading, always

Every agent this skill spawns gets both of these pasted into its prompt:

1. **The PRD** published to Linear by `/to-spec`. Fetch the body and pass it
   inline. Do not pass a URL and hope the agent fetches it.
2. **`CONTEXT.md` and `docs/adr/`**, the domain glossary and the architecture
   decisions. Agents use this vocabulary in code, tests, commits, PR bodies, and
   Linear comments.

An agent handed neither has been briefed wrong. Spawn it again.

---

## Phase 0: Bind

A wrong binding writes to the wrong Linear project and is expensive to unpick.

1. **Repo.** `git rev-parse --show-toplevel`, `git remote -v`, current branch,
   `git status --short`. A dirty tree stops the run: ask the user to commit or
   stash first.
2. **Integration branch.** Cut `factory/<feature-slug>` from the default branch
   and record it. Every ticket merges into this branch. The factory never merges
   to the default branch itself.
3. **Tracker config.** Read `docs/agents/issue-tracker.md` and
   `docs/agents/domain.md`. Missing? Run `/setup-matt-pocock-skills` first.
4. **Linear.** Resolve the project from the map in the user's `CLAUDE.md`. If the
   repo is not in that map, ask which project. Never invent one. Confirm the
   parent or spec issue by ID out loud before writing anything. List the team's
   existing states and labels, and agree with the user which existing one marks a
   parked ticket. If none fits, a park lives in the ledger and in a comment on the
   issue. Never create a state or label to cover the gap.
5. **GitHub.** Confirm `gh auth status` and that the remote exists. With no
   remote, say so plainly and run in local commit mode rather than inventing a PR
   flow.
6. **Stack and CI.** Detect language, package manager, test runner, typecheck
   command, lint command, dev server command. Record the exact commands: agents
   run these, so they have to be real, not plausible. Check `.github/workflows`
   and record whether CI exists. **With no CI, the integrator runs the suite
   itself and never merges on a builder's self reported counts.**
7. **Ledger.** Create it at an absolute path **outside the repo and outside every
   worktree**: `%TEMP%\factory\<feature-slug>\factory.md`, from
   [ledger.md](./ledger.md). Pass that path verbatim to every agent. **Only the
   orchestrator writes it.** Others report their lines upward, so no checkpoint
   ever lives in a worktree that gets deleted.
8. **Approve the Linear comment template.** Show the user the template from
   [closing.md](./closing.md) once. On approval, agents post filled copies for the
   rest of the run without asking again. This is the only standing exception to
   the user's draft first rule, it covers this run only, and it covers progress
   comments only. Anything outside the template still gets drafted.

Report the binding as a short table and wait for a go.

## Phase 1: Tickets

Run `/to-spec`, then `/to-tickets`, in your own context in one unbroken stretch,
because they build on the same thinking. This is the one phase where you read the
repo yourself. After it, you never touch source again.

The user approves the ticket breakdown here. It is their second and last
scheduled interruption before the build runs.

Publish to Linear in dependency order with native blocking links and the
`ready-for-agent` label. Copy every ticket into the ledger with its Linear ID,
its blockers, and a lane.

## Phase 2: Hire the team

Write the agent definitions from [team.md](./team.md) into `.claude/agents/`,
tailored to the stack from Phase 0. Lanes come from the shape of the actual work.

Then try `subagent_type: factory-builder-<lane>`. If that type is not recognised
because the new definitions have not been picked up yet, fall back to
`general-purpose` and paste the agent file's body into the prompt. Both work. Say
which one you are using, once.

**Models are fixed.** The orchestrator, meaning you, runs on Opus, because
judgement about what to park and what to dispatch is the one thing that cannot be
delegated. Every agent below you runs `model: sonnet`, with no exceptions:
builders, adversaries, the integrator, polish, and every one off subagent a
builder spawns. Do not raise an agent to Opus because a review came back shallow.
A shallow review is a briefing problem. Fix the brief.

## Phase 3: The loop

Repeat until the frontier is empty.

**Compute the frontier.** Every ticket whose ledger status is **Todo** and whose
blockers are all **Done**. A ticket that is In progress, Adversary, Fixing,
Merging, Done, Parked, or Failed is never on the frontier. If the frontier is
empty while unparked Todo work remains, the blocking graph is wrong. Say so and
stop.

**Confirm the line is green.** Never dispatch off an integration branch whose
suite is red. A red line halts the run.

**Dispatch the whole frontier at once.** Every frontier ticket goes out in one
message so they all run concurrently. Do not batch them into groups. Do not hold
a ticket back because another one is still building. The blocking graph already
decided what may run in parallel, and it is the only thing allowed to serialise
the work.

```
Agent(
  subagent_type: "factory-builder-<lane>",
  isolation: "worktree",
  run_in_background: true,
  model: "sonnet",
  prompt: <brief from team.md + PRD + CONTEXT.md + ADRs + ticket body>
)
```

**The cap is ten live agents**, counted across the whole tree: builders,
adversaries, the integrator, and every one off subagent a builder spawns.

When the frontier is wider than the cap allows, dispatch down to ten and hold the
rest in a queue, ordered by how many tickets each one unblocks. **The moment an
agent finishes and returns its slot, dispatch the next ticket off the queue.**
Never wait for the current group to drain before starting the next one. That
barrier is the difference between a build that finishes overnight and one that
does not, and there is nothing to gain from it.

So the behaviour falls out on its own: a frontier of six runs all six at once, a
frontier of twenty runs ten and feeds the rest in as slots open, and a fully
saturated run finishes tickets one at a time. Sequential by consequence when the
cap bites, never sequential by design.

A builder cannot see the global count, so **tell it its allowance in the brief**:
a number from 0 to 3, decided by how much slack the cap has when you dispatch it.
Reserve those slots against the count the moment you hand them out. Never write
"spawn subagents if there is room" and expect an isolated worktree to work out
what room means.

**One integrator at a time.** Merging is a single lane into a shared branch. A
second integrator will pull a base commit that goes stale under it, run its green
check against that stale base, and either fail the push or land a merge out of
blocking graph order. Builders and adversaries run as wide as the cap allows;
merges queue.

**Re-check the line before every dispatch, including a slot triggered one.** A
merge can turn the integration branch red between two dispatches. A builder that
cuts its worktree from a red base is doing work you will throw away.

A builder that has not reported in 30 minutes is Failed: stop it with `TaskStop`,
then **remove its worktree and delete its branch before redispatching**
(`git worktree remove <path> --force`, then `git branch -D`). Skip that and the
retry dies on `already checked out` rather than on the ticket, burning the second
attempt for nothing. A ticket is dispatched at most twice; the second failure
parks it.

**Ceiling.** Keep the running totals in the ledger current: builders spawned,
subagents spawned, live agents right now, queue depth. Stop and report when total
agents spawned passes **four per ticket in the run**. That is a thrash detector,
not a size limit: a clean ticket costs three agents (builder, adversary,
integrator), so crossing four means fix rounds and retries are eating the run. A
flat number would halt a twenty ticket build partway through for no reason.

**Each builder, inside its own worktree:**

1. Branch `factory/<linear-id>-<slug>` off the integration branch.
2. Build through `/tdd` at the seams the PRD names. One red green slice at a time.
3. Spawn one off subagents for self contained work, up to the allowance its brief
   states, never more: reading an unfamiliar library, generating fixtures,
   scaffolding components.
4. Typecheck and run affected tests as it goes, the full suite once at the end.
5. Run the secret scan in [team.md](./team.md), then push and open a draft PR
   against the integration branch using the body template in
   [closing.md](./closing.md).
6. Report back: ticket ID, branch, PR number, test counts, subagents spawned,
   decisions taken, questions parked, the secret scan command and its output.

**Then the adversary.** For each green PR, spawn `factory-adversary` on the diff.
It hunts secrets and logic that is wrong, not style that is unfashionable. It
reports findings ranked by severity, or reports nothing, which is a valid and
common answer.

**Then the fix round.** Send confirmed findings back to the same builder with
`SendMessage` so it keeps its context. One fix round. If findings survive a second
adversary pass, park the ticket rather than looping a third time.

**Then integrate**, one at a time. Ready PRs queue for the single integrator
lane. `factory-integrator` re-pulls the integration branch, confirms the suite is
green on that fresh base (CI where it exists, otherwise by running the recorded
commands itself), marks the PR ready,
and merges it into the integration branch in blocking graph order. Before deleting
a branch it removes the worktree holding it (`git worktree remove <path> --force`),
then deletes the branch, and runs `git worktree prune` after each merge. Conflicts
are the integrator's job. If a merge turns the suite red though both sides were
green alone, it reverts the merge commit immediately (`git revert -m 1 <sha>`),
parks the ticket, and reports.

**Then close out.** Post the approved Linear comment, flip the issue to Done, and
promote newly unblocked tickets from Backlog to Todo. Status changes need no
approval.

**Checkpoint after every single ticket.** Write the ledger before you report
anything. A session that dies mid run must resume without redoing finished work.

## Phase 4: Surface

Only when the frontier is empty and parked questions remain.

Present every parked question in **one** message. Per question: the ticket it
blocks, what is unresolved, the options, your recommendation, and what stays
blocked until it is answered. Use `AskUserQuestion` when the options are
enumerable, prose when they are not.

Answers go into the ledger's decision log, the affected tickets return to Todo,
and you return to Phase 3.

Never surface a question you could have decided. The parking rules are in
[ledger.md](./ledger.md).

## Phase 5: Ship

1. Full suite, typecheck, and lint on the integration branch. Report real counts.
2. Spawn `factory-polish` against the MVP bar in [closing.md](./closing.md), on
   its own branch cut from the integration branch. Its PR gets an adversary pass
   like any other diff before it merges.
3. Remove every remaining factory worktree. Confirm `git worktree list` shows only
   the main checkout.
4. **Open one pull request from the integration branch to the default branch**,
   with the full ticket list and test counts in the body, and leave it for the
   user. Merging it is their call, not yours.
5. Post the closing comment on the Linear parent issue and move it to Done.
6. Write the HTML report to `%TEMP%` per the user's `CLAUDE.md` task completion
   rules, open it, and give the absolute path.
7. In chat, give the verdict only: tickets shipped, tickets parked, test counts,
   what the user has to decide. The report carries the detail.

## Resume

`/factory resume` reads `%TEMP%\factory\<slug>\factory.md`, rebuilds the frontier
from the ledger and live Linear state, and re enters Phase 3. It redoes nothing
the ledger records as Done.

## Rules that never bend

- After Phase 1, the orchestrator never reads source files, never edits, never
  runs tests. The moment you do the work yourself, the context budget is gone.
- Nothing reaches the default branch without a pull request the user merges.
- No ticket merges without an adversary pass on its diff.
- No Linear write outside the approved template without showing a draft. Never
  create a Linear state, label, project, or team.
- No secrets, keys, or log evidence in any commit, PR body, or Linear comment.
  Scan before every push, with a real command.
- Report failures as failures, with the output. A parked ticket is not a shipped
  ticket and never gets counted as one.
