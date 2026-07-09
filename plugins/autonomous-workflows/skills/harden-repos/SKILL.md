---
name: harden-repos
description: Harden several repos in one pass by fanning out one fresh-context sub-agent per repo. Each agent secret-scans, reviews, applies only verified-safe fixes on a branch (never pushing), and reports back; the orchestrator aggregates everything into HARDENING_HANDOFF.md. Use when the user says "harden my repos", "harden-repos", "clean up all my repos", "run hardening across the portfolio", or wants multi-repo cleanup without hitting context limits.
---

# Harden Repos

Clean a whole portfolio in one pass. The point of fanning out — one sub-agent per repo, each with its own fresh context — is that no single context has to hold every repo at once, which is what makes long multi-repo cleanups fail partway through. Each agent works in isolation, changes nothing it can't verify, and never pushes. You get one report and a set of review-ready branches.

## Steps

1. **Resolve the target repos.**
   - If the user passed repo paths as arguments, use exactly those.
   - Otherwise read `~/.harden-targets` — one repo path per line, `#` comments allowed. This file is the user's maintained list; it is personal and must not live inside this skill or any shared repo.
   - If neither exists, don't guess and don't scan the whole disk. Ask the user which repos to harden and offer to seed `~/.harden-targets` from their answer.
   - Confirm the resolved list back to the user before spawning anything.

2. **Fan out — one sub-agent per repo, in parallel.** Spawn all agents in a single batch (multiple Agent calls in one message) so they run concurrently. Each agent gets a different repo directory, so there are no file conflicts and no worktree isolation is needed. Give each agent the brief in step 3, with its repo path filled in. Ask each to return a structured report (the shape in step 4).

3. **Per-repo agent brief.** Each sub-agent does this, in order, inside its one repo, and stops at the first hard blocker:
   - **Secret scan first.** Grep the working tree and staged content for keys, tokens, `.env` values, credentials, private keys. If anything sensitive is found, **do not stage or commit anything** — record it as a blocker and skip straight to the report. Secrets abort hardening for that repo.
   - **Establish a test baseline.** Detect the test command (npm/pytest/cargo/go per the repo's manifests) and run it once. Record passed/skipped/total. If there's no suite, note "no tests" — this restricts what may be applied.
   - **Review.** Read the code for real issues: complexity worth simplifying, missing `.gitignore` entries, absent pre-commit tooling, dead code, obvious bugs. Collect findings.
   - **Branch, then apply only verified-safe fixes.** `git switch -c "harden/$(date +%Y-%m-%d)"`, then:
     - *Mechanical-safe fixes* (formatting, adding a pre-commit config, `.gitignore` entries, import ordering) may always be applied — they don't change behavior.
     - *Behavioral fixes* (simplifications, refactors) may be applied **only if the repo has tests.** After applying, re-run the suite. Green with the same passed/skipped counts → keep it. Red → revert that change and downgrade it to a suggestion in the report. Never leave a red branch.
     - *No test suite* → apply mechanical-safe fixes only; every behavioral idea becomes a report-only suggestion, applied by nobody.
   - **Commit locally, never push.** Secret-scan the diff again, run the repo's pre-commit hook, and commit as `harden: <summary>`. No `git push`, no PR.
   - **Return the report.**

4. **Per-repo report shape.** Each agent returns:
   - repo name, branch created (or none)
   - secret-scan result (clean / **BLOCKED** with what was found — never the secret value itself)
   - findings: issues discovered
   - applied: fixes committed (with the test result proving green)
   - suggested-only: behavioral changes not applied, and why (no tests, or reverted on red)
   - test status: before → after counts
   - needs-manual: anything requiring the user

5. **Aggregate into `HARDENING_HANDOFF.md`.** After all agents return, write one file (in the current working directory, or where the user asks) summarizing every repo: what was found, what was applied on which branch, test status, and — called out at the top — any repo that is **BLOCKED on secrets** or otherwise needs manual attention. This file is the single thing the user reads to know the state of the whole portfolio.

6. **Close out.** Tell the user: which repos got a `harden/<date>` branch to review, which are blocked, and that nothing was pushed. Point them at `HARDENING_HANDOFF.md` and suggest `remediate` or a manual review for anything left as suggested-only.

## Notes

- Nothing is ever pushed. Every repo ends on a local branch the user reviews.
- A secret finding is a hard stop for that repo — flag it, never stage it, and never print the secret's value into the report.
- "Verified-safe" is the whole game: a behavioral change is only kept if the suite proves it didn't break anything. No tests means no behavioral changes, only mechanical ones.
- One agent per repo, fresh context each — that isolation is what lets this cover a large portfolio in a single run without the context-limit failures that sink sequential passes.
- If a repo path in the target list doesn't exist or isn't a git repo, note it in the handoff and move on; don't abort the whole run.
