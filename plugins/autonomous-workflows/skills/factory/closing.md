# Closing the loop

Two audiences. GitHub gets the mechanics of the change. The vault ticket gets the
narrative of the work. Neither gets a copy of the other.

## PR body template

Opened as a draft by the builder against the run's integration branch, marked
ready by the integrator. Nothing auto closes a vault ticket: the integrator
closes it explicitly after the merge with `ticket.py status <KEY>-<n> Done`. Keep
the `Closes` line anyway, it names the ticket in the PR, and add the PR to the
ticket with `ticket.py set <KEY>-<n> prs <every existing PR> <url>`. `set` replaces the
whole list, so pass the PRs already on the ticket too.

```markdown
## What this delivers

<the end to end behaviour that now works, from the user's perspective, two or
three sentences. Not a layer by layer list.>

Closes <KEY>-<n>

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

## Ticket comment template

**Approved once in Phase 0, then used unedited for the whole run.** Agents fill
it in and post without further approval. Anything that does not fit this shape
gets drafted for the user in the normal way.
Post a filled copy with `ticket.py comment <KEY>-<n> --body-file <file>`.

```markdown
**Shipped** via [#<pr>](<pr url>) on `<branch>`.

<one or two sentences on what now works, in the project's own vocabulary.>

- Tests: <n> passed, <n> failed
- Adversary: <clean / n findings, fixed>
- Decisions: <one line each, or "none">

Unblocks: <KEY-nnn, KEY-nnn, or "nothing">
```

For a blocked ticket, the same comment slot gets this instead, and the ticket stays
in its current state rather than moving to Done:

```markdown
**Blocked** by <KEY-nnn>, filed with the question, the options, and everything
the run tried before filing it.

<the question, in one line.>

Work completed before blocking: <what landed, or "none, blocked before any code">.
Blocks: <KEY-nnn, KEY-nnn, or "nothing">
```

## The blocker ticket body

Approved once in Phase 0, then filed unedited.
One new ticket in the same project per blocked question
(`ticket.py new --title "<title>" --project "<project>" --body-file <file>`),
linked as a blocker of the stopped ticket
(`ticket.py relate <KEY>-stopped blocked_by <KEY>-blocker`).

```markdown
Title: <the question, one line>

**What is unresolved:** <two sentences, no more>

**Options:** <a> / <b> / <c>

**Recommendation:** <one of them>, because <one line>.

**Tried before filing this:**

- Attempts (in order): <one line each, what was tried and what came back. The
  last line is the resolver's UNRESOLVED report, verbatim.>

**Blocks:** <KEY-nnn>
**Work completed first:** <branch and PR, or "none">
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

**The bar above assumes a greenfield prototype. On an existing codebase it is
wrong, and applying it whole is how a run ends by rewriting a README nobody asked
it to touch.** A mature repo already made its choices about visual system, README,
and seed data, and the factory did not earn the right to revisit them by merging
four small tickets into it.

So on an existing repo, cut the brief down before spawning polish:

- **Restrict it to the files this run actually touched.** Name them in the brief.
  Everything else is out of bounds, however rough it looks.
- **No features, no new files, no dependency, no ticket writes.** Out of scope
  ideas come back as proposed tickets in its report, for the user to decide. It
  files nothing itself.
- **Say in the brief that finding nothing is a correct outcome.** Otherwise it
  will find something to justify its existence, and on a clean diff that means
  inventing work. A polish pass that reports "nothing to polish" and names what it
  checked has done its job.
