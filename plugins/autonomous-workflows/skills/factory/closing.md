# Closing the loop

Two audiences. GitHub gets the mechanics of the change. Linear gets the narrative
of the work. Neither gets a copy of the other.

## PR body template

Opened as a draft by the builder against the run's integration branch, marked
ready by the integrator. Because the base is not the default branch, `Closes`
will not auto close the Linear issue: the integrator closes it explicitly after
the merge. Keep the reference anyway, it is what links the PR to the ticket.

```markdown
## What this delivers

<the end to end behaviour that now works, from the user's perspective, two or
three sentences. Not a layer by layer list.>

Closes ELI-<n>

## Acceptance criteria

- [x] <criterion, quoted from the ticket>
- [x] <criterion>

## How it was built

<the seam it was tested at, and why that seam. Any prefactor that landed first.>

## Decisions taken without asking

- <one line each, matching the ledger's decision log>

## Tests

`<exact command>`: <n> passed, <n> failed
`<typecheck command>`: clean

## Secret scan

`<exact command>`: <no matches, or what was found and how it was removed>

## Adversary review

<clean, or the findings and what was changed in response>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Commit messages: present tense, one logical change each, ending with the
attribution line the session's harness specifies.

## Linear comment template

**Approved once in Phase 0, then used unedited for the whole run.** Agents fill
it in and post without further approval. Anything that does not fit this shape
gets drafted for the user in the normal way.

```markdown
**Shipped** via [#<pr>](<pr url>) on `<branch>`.

<one or two sentences on what now works, in the project's own vocabulary.>

- Tests: <n> passed, <n> failed
- Adversary: <clean / n findings, fixed>
- Decisions: <one line each, or "none">

Unblocks: <ELI-nnn, ELI-nnn, or "nothing">
```

For a parked ticket, the same comment slot gets this instead, and the issue stays
in its current state rather than moving to Done:

```markdown
**Parked** pending a decision.

<the question, in one line.>

Work completed before parking: <what landed, or "none, parked before any code">.
Blocks: <ELI-nnn, ELI-nnn>
```

## The MVP bar

The target is a prototype that reads as a polished v3 and scopes as a v0.1.
`factory-polish` enforces this in Phase 5 and may not add features to reach it.

**In scope for polish:**

- Every screen, route, or command has real content. No lorem, no `TODO`, no
  placeholder that ships.
- Empty, loading, and error states exist and say something useful. "No runs yet.
  Import a CSV to get started" beats a blank div.
- One visual system: a single spacing scale, two typefaces at most, a neutral
  palette plus one accent. Consistency reads as polish more than any single
  flourish does.
- Keyboard focus is visible, inputs have labels, contrast passes. Cheap, and its
  absence is the loudest tell of a spike.
- Seed or demo data so the thing looks alive the first time it runs.
- A `README.md` whose setup and run commands actually work on a clean clone.
  Verify by running them.
- The happy path works end to end without a console error.

**Out of scope, and say so rather than doing it:**

- Auth hardening, rate limits, production secret management
- Internationalisation, theming beyond light and dark, animation systems
- Performance work absent a measured problem
- Exhaustive edge case coverage, admin panels, settings screens nobody asked for
- Anything that needs a new route, model, or dependency. That is a ticket for the
  next run, and `factory-polish` files it rather than building it.

The line: polish makes what exists look deliberate. It never makes there be more.
