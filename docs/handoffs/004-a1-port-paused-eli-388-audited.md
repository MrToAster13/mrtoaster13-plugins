# Handoff 004: a1 port paused under the tdd-loop rule, ELI-388 audited

Status: paused. The batch-run port needs a ticket with acceptance criteria before it can go through tdd-loop.

## Goal
Elijah answered "dealers choice" to the two questions at the end of handoff 003. This session picked:
- (a) a1 with "port yes". claude-config owns the six shared skills. This repo keeps a hand-ported public copy, and batch-run gets ported in both directions now.
- ELI-388: "388 branch". Use Mini's branch `origin/eli-388-sell-ai-setup` (5ae356c) in the Portfolio repo. Do not merge it until Elijah approves the copy.

## Rule change mid-session (2026-10-21)
Elijah replaced the audit-then-push loop:
- Every code change goes through the tdd-loop skill. The ticket's acceptance criteria are the spec and seams. Ask Elijah only when a ticket has none.
- Docs, comments and config text with no behavior to test get only the security stage, then a commit.
- Commit only. Elijah pushes and merges.
- On a hard stop, leave the work uncommitted, write the reason here and wait.

Nothing was pushed after the rule change. Earlier pushes this session were under the old rule: plugins PRs #9 and #10, claude-config PR #20, all merged.

## What happened
- The batch-run port agent was stopped partway. It was not running tdd-loop, and the port has no ticket, so it has no acceptance criteria.
- The partial work sits uncommitted in two worktrees:
  - A local worktree of this repo, branch `port/batch-run-two-way` off faa769d. 7 files, +446/-82.
  - A local worktree of claude-config, branch `port/batch-run-two-way` off 94a4981. 7 files, +1978/-649. The large count is likely CRLF churn; this is unverified.
  - The batch-run tests pass in both: 305 passed, 0 failed (main has 239). No adversarial review or security stage has run on it, so treat it as a draft.
  - ADR 0009 and the README ownership line were not written.
- ELI-388: an Opus agent audited 5ae356c read-only. The build passes. Two must-fix items: the "his own skills" / "I write my own skills" claim cites a source that ELI-53 retracted, and site.ts:522 says "his" in otherwise first-person copy. The full findings are in a comment on ELI-388 (vault commit 3aff94d). No PR was opened.

## Next (in order)
1. Elijah decides how the batch-run port gets acceptance criteria: a new ticket (a draft is needed first) or criteria stated in chat. Then run tdd-loop in the two worktrees. The `~/.claude` hardening to port into the plugin: NaN and Infinity rejection, the `load_units` slug-collision guard, and the `estimate.project()` input checks. Going the other way: pl-1, pl-2, the case tag in `slug_for` and the `read_record` unit_id check. The agent's diff list may be incomplete.
2. Docs-only, security stage then commit: ADR 0009 in claude-config, and README.md:48 here saying claude-config maintains the shared skills.
3. Elijah approves or edits the ELI-388 copy. Fixing the two must-fix items is a code change to site.ts, so it goes through tdd-loop or counts as copy text. Elijah decides which.
4. Follow-ups carried from handoff 003: the factory plural exemption, estimate.py's private `state._*` calls, and the factory, handoff and resume hand ports.

## Gotchas
- Do not touch hermes-plugin-claude-subscription-directsdk.
- The Portfolio repo's `node_modules` is empty. The audit ran `npm ci` inside a throwaway worktree and removed it afterward.
- Remote branches `arch/pl-1-model-policy` and `arch/pl-2-config-store` still exist on origin. They were not deleted this session.

## Update 2026-10-28, ELI-676

- This branch was merged into main through the ELI-645 audit branch, so `docs/handoff-004` holds nothing main lacks.
- The batch-run two-way port never reached a commit or a remote branch. No `port/batch-run-two-way` branch exists on origin in either repo. The two local worktrees could not be checked from the audit host on 2026-10-28, so whether they still exist is unverified. Treat that work as abandoned.
- ELI-676 took over one part of it: `slug_for` in claude-config now tags uppercase ids and the `batch-config` stem the way this copy does, and `tests/test_cross_copy.py` in both repos resumes a checkpoint the other copy wrote. The rest of the port (strict JSON parsing, the steal guard, `estimate.py` checks one way; pl-1, pl-2, Windows sharing retries the other) is still open and listed as a fork in claude-config `bin/shared-skills.json`.
- claude-config now maintains the shared skills. `bin/sync-shared-skills.py` there writes the copies it lists, which is `fleet-health` in this repo so far, and `.github/workflows/shared-skills.yml` here fails when a generated file is edited by hand.
