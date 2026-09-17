---
name: tdd-loop
description: Autonomous test-driven loop for a single feature: write failing tests, iterate implementation until green (max 10 attempts, each logged), run an adversarial review sub-agent that hunts logic bugs like over-aggressive filters, auto-fix and re-verify, run a security check, then commit only when everything passes. Use on "tdd loop", "build this feature test-first and don't stop until it's green", or when handed a feature spec to implement autonomously.
---

# Autonomous TDD Loop

One feature, start to commit, without checking in at every step. The loop is bounded: 10 implementation attempts, every attempt logged, and a hard stop rather than a commit whenever a gate fails.

If a `tdd` skill is installed, read it before writing any test; this skill is the orchestration around it and does not restate it. Without one, hold to these rules:

- A test observes behaviour through a public seam (a function, an endpoint, a CLI), never a private helper.
- One behaviour per test. A test that checks five things names none of them when it fails.
- Do not mock what you own. Mock the network and the clock, not your own modules.
- A test that never fails is not a test. Watch each one go red before it goes green.

Stack commands come from the repo's own config: `package.json` scripts, `pyproject.toml`, `tox.ini`, `Makefile`, a CI workflow. Do not invent lint or test invocations.

## Stage 0: Bind

1. **Repo.** `git rev-parse --show-toplevel`. Fails → not a git repo, stop and say so. Every later command runs from this root.
2. **Stack.** Node if `package.json`; Python if any of `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements*.txt`, `Pipfile`, `tox.ini`. Both can be true. Neither → ask.
3. **Baseline.** Run the full suite before touching anything. Record pass/fail counts. Already-failing tests are pre-existing and excluded from every later gate, so say which ones.
4. **Feature.** If the user supplied a spec, use it. Otherwise scan for candidates and present them for a pick:
   - open issues in the tracker this repo maps to, if the user keeps one (Linear, GitHub issues)
   - `TODO.md`, `BACKLOG.md`, `ROADMAP.md`, or a `## TODO` section in the README
   - tests marked skip/xfail/`it.todo`
   Never guess which feature is "next". Present, wait, then proceed.
5. **Seams.** Write down the public interfaces the tests will observe, and confirm them with the user. No test is written at an unconfirmed seam.

## Stage 1: Red

Derive acceptance criteria from the spec, one line each. One test per criterion, plus boundary and error cases. This is a deliberate batch, and it is the one place this skill departs from `tdd`'s vertical slice rule, so bound it: **tests must map 1:1 to written acceptance criteria.** No speculative tests for behavior the spec doesn't ask for.

Run the suite. Every new test must fail, and must fail for the **right reason**: missing behavior, not a syntax error, bad import, or missing fixture. A test failing on `ImportError` proves nothing. Fix the scaffolding until each new test fails on a real assertion, then record the red count.

## Stage 2: Green loop

Budget: **10 iterations**, shared with Stage 4. Each iteration:

1. Pick the smallest failing test that unblocks the most others.
2. Write only enough code to pass it. No speculative features.
3. Run the full suite.
4. **Log the attempt**: iteration number, files touched, one-line summary of the change, pass/fail counts, names of tests still red.

Exit green when every new test passes and no pre-existing test regressed.

Hard stops before iteration 10:

- **No progress.** Two consecutive iterations with an identical failing set → stop, report what's stuck, hand back.
- **Test tampering.** Never edit an assertion to make it pass. If a test looks genuinely wrong, stop and take it to the user with the reasoning. Changing a test to match the code inverts the whole point of the loop.
- **Budget exhausted.** 10 iterations without green → stop, report the log, do not commit.

## Stage 3: Adversarial review

Spawn a sub-agent (model: `sonnet`) with the brief in [`adversarial-review.md`](adversarial-review.md). Its job is to break the implementation, not to praise it. Green tests are the input, not the verdict. The bugs that matter here are the ones the tests were written to miss.

Every finding must carry a concrete failure scenario: specific inputs or state, and the wrong output or crash that results. **A finding with no reproducible scenario is dropped.** Report how many were dropped so silent filtering doesn't happen at this layer either.

## Stage 4: Fix and re-verify

For each confirmed finding, in severity order:

1. Write a regression test that reproduces it. **Run it and watch it fail.** A regression test that passes before the fix isn't testing the bug.
2. Fix the code.
3. Run the full suite.
4. Log it as an iteration, same format as Stage 2, drawing from the same 10-iteration budget.

A fix that breaks another test is not done. Loop until the suite is green with every regression test included.

## Stage 5: Security

1. `cd` to the repo root first, or `security-review` silently narrows its scope. Then invoke it.
2. **Secrets.** Scan changed and staged files for credentials, API keys, tokens, private keys, `.env` contents, SSH logs. Anything suspicious → stop, show it, do not commit.
3. **Script permissions.** `git ls-files -s`; any tracked `.sh` or shebang-bearing file at mode `100644` breaks on Linux after a push. `git update-index --chmod=+x <path>` each one and report which flipped.

## Stage 6: Commit

Commit only when **all** hold:

- full suite green, including every regression test
- zero unresolved adversarial findings
- security check clean

Any one failing → stop and hand back uncommitted, with the reason. Never commit over a red gate.

Commit style: match `git log --oneline -20`. Present tense, one logical change, message states intent rather than listing files. Native hooks, so never `--no-verify`, `--no-gpg-sign`, or `--amend`. **Do not push.**

## Output

Always show the iteration log in chat as a table: iteration, stage, change, pass/fail, tests still red. That is the headline the user asked for.

Then write the full report to the OS temp dir as `tdd-loop-<feature>-<YYYY-MM-DD-HHMMSS>.html` (self-contained HTML, iteration log as a table, one card per adversarial finding), open it, and give the absolute path. Chat gets the verdict: green or not, what committed, what needs a decision.
