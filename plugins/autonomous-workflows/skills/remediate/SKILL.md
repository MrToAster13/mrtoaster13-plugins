---
name: remediate
description: Self-driving, test-verified remediation loop. Reads REMEDIATION_PLAN.md (or drafts one and gets your OK), then implements each phase, runs the full suite, and iterates until green — without editing tests to fake it. Commits each green phase to a branch and drafts a ready-to-run push/PR. Use when the user says "remediate", "run the remediation plan", "fix until tests pass", "self-drive these fixes", or points at a plan of bugs to work through.
---

# Remediate

Drive a plan of fixes to completion without supervision, and stop honestly when a fix is beyond reach instead of faking success. The one rule that makes this safe: **green must come from fixing code, never from weakening tests.** An agent told "don't stop until green" will, when stuck, delete an assertion or add a `.skip` to force a pass. This skill forbids that structurally.

You run unattended between two human gates: the user approves the *plan* up front, and triggers the *push* at the end. Everything in between is yours.

## Steps

1. **Get the plan, then gate on it.** Look for `REMEDIATION_PLAN.md` at the repo root.
   - If it exists, read it. Each phase is one checklist item.
   - If it's missing, scan the repo for real defects (failing tests, obvious bugs, error-prone spots the user named), draft a `REMEDIATION_PLAN.md` in the format below, show it to the user, and **wait for explicit approval before doing any work.** This is the only start-of-run gate; do not skip it.
   - Load every phase into TodoWrite, one todo per phase, so progress is visible.

   Plan format:
   ```markdown
   # Remediation Plan

   ## Phase 1: <short title>
   - What's wrong: <symptom / failing test / bug>
   - Fix: <the intended change, in source only>
   - Done when: <the observable condition, e.g. "test_x passes, suite green">
   ```

2. **Detect the test command once.** Auto-detect and reuse it for every phase:
   - `package.json` with a `test` script → `npm test` (or `pnpm test` / `yarn test` per lockfile).
   - `pytest.ini`, `pyproject.toml`, `tox.ini`, or a `tests/` dir → `pytest -q`.
   - `Cargo.toml` → `cargo test`. `go.mod` → `go test ./...`.
   - If you can't tell, ask the user for the exact command rather than guessing.

3. **Snapshot test integrity before you touch anything.** Run the suite once and record three numbers: **passed, skipped, total**. This baseline is the anti-cheat contract. Also list the test files (anything matching `test_*.py`, `*_test.go`, `*.test.*`, `*.spec.*`, or under `tests/`, `__tests__/`, `spec/`). You will not edit these files.

4. **Work each phase as a loop, capped at 3 attempts.** For a phase:
   1. Implement the fix in **source only**. If the phase genuinely cannot be fixed without changing a test (e.g. the test encodes wrong behavior), **stop and ask** — do not edit the test yourself.
   2. Run the full suite.
   3. Read the result:
      - **Green, and skipped count did not rise, and no test file changed** → phase passes, go to step 5.
      - **Red on an assertion / real failure** → diagnose the root cause and fix the source. This counts as one attempt. Never re-run a red assertion hoping it flips — a failing assertion is a real bug, not noise.
      - **Red on an environmental error** — the message matches `ENOTFOUND`, `ETIMEDOUT`, `ECONNRESET`, `ECONNREFUSED`, `socket hang up`, `EAI_AGAIN`, HTTP `429`, or a DNS/rate-limit signature → retry up to **3 times with exponential backoff (2s, 8s, 20s)**. This does **not** consume a phase attempt. If it still fails after 3 retries, treat the environment as down, stop, and report it.
   4. After 3 real attempts on a phase without green, **stop the whole run** (see step 7). Do not move to later phases — they may depend on this one.

5. **Simplify, lightly, under the gate.** Before committing a green phase, do one pass for obvious non-behavioral cleanup (dead code, duplication, naming) in the source you just touched. Re-run the suite; it must still be green with the same passed/skipped counts. If simplifying turns anything red, revert the simplification and commit the plain fix.

6. **Commit the phase to a branch.** On the first phase, create the branch: `git switch -c "remediate/$(date +%Y-%m-%d)"`. For each green phase:
   - **Scan the diff for secrets before staging** (keys, tokens, `.env` values, credentials). If anything looks sensitive, stop and flag it — never stage it.
   - Stage the source changes, run the repo's pre-commit hook, and commit with a clear message: `remediate: <phase title>`.
   - One commit per phase. Mark the todo complete.

7. **Stop honestly, and report.** The run ends one of two ways:
   - **All phases green:** you're done. Go to step 8.
   - **A phase failed after 3 attempts (or the environment is down):** the green phases are already committed, so the branch is a clean checkpoint. Stop here and report: which phase is stuck, your root-cause diagnosis, the diffs you tried and why each failed, and the single most promising next step. Do not silently continue.

8. **Draft the push and PR — but do not run them.** End the run by handing the user ready-to-execute commands with everything filled in, so pushing is one paste. Never push or open the PR yourself.
   ```bash
   # Review, then run these to publish:
   git push -u origin "remediate/$(date +%Y-%m-%d)"
   gh pr create \
     --title "Remediation: <n> phases, <passed> tests green" \
     --body "<generated summary: phases done, before/after test counts, anything still open>"
   ```

9. **Final summary.** Report: phases completed, per-phase one-liners of what changed, the before/after test counts (proving the skip count didn't move), the branch name, and — if the run stopped early — exactly what's left.

## Notes

- **The anti-cheat contract is non-negotiable.** If the passed count goes up only because the skipped or total count changed, that is not progress — halt and report it. A suite that was `96 passed` must end `>= 96 passed, 0 new skips`.
- Test files are read-only for this skill. Legitimate test changes are real work that belongs in its own reviewed commit, not buried inside an unattended fix loop.
- Environmental retry is only for errors *thrown* by the environment (network, DNS, rate limits). A test that runs and asserts the wrong value is always a real bug.
- Never push, never open a PR, never force anything. The user holds the last gate.
- If there's no test suite at all, this skill has nothing to verify against — say so and stop, rather than committing unverifiable "fixes."
