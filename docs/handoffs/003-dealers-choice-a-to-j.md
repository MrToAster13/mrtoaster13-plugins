# Handoff 003: Elijah's "dealer's choice" on questions (a) to (j)

Status: done, except (a), which needs Elijah to approve a design, and ELI-388, which needs Elijah to approve the copy.

## Goal
Elijah answered the batched questions from handoff 001 (a to f), from the chat (g, h) and from the end of handoff 002's round (i, j) with "dealers choice". This session picked each one, did the work it led to, and audited every push.

## Picks and outcome
- (a) Which repo owns the six duplicated skills: design first, no implementation. The design is under Next 1 and waits for Elijah.
- (b) clear-not-compact in the marketplace: keep it, deprecated. No change.
- (c) `~/.claude/HANDOFF-fleet-rules.md`: keep. No change.
- (d) Delete fuzz9.out: yes. Backed up to `%TEMP%\backup-20261021\fuzz9.out`, then deleted from the repo root. It was never on a branch, but T3 checkpoint commits such as a2d49a9 still hold a copy.
- (e) Delete claude-config's `origin/eli-500-point-skills-away-from-claudemd` and `origin/fleet/claude-code-plugins`: yes, done. Both pointed at 05a2ac2, an ancestor of claude-config main, so that sha still holds their content.
- (f) Add the two fleet-rules handoff items to claude-config CONTEXT.md: yes. Merged in claude-config PR #20 (merge 94a4981). The text attributes both items to the handoff. It notes that sha 9ef3a0a is not in claude-config's history.
- (g) Add the worked-ticket status exemption to the triage and wayfinder skills: yes, in PR #20. Wayfinder has the exemption. Triage does not, because a triage session has no worked ticket of its own. Triage now says every status change and close needs a draft, which is stricter than CLAUDE.md. The Opus audit found the first wording required drafts for comments on other tickets, and that triage's "worked ticket" was ambiguous. Both were fixed. Round 3 was clean. claude-config suite: 930 passed, 12 skipped, and the hygiene CI passed.
- (h) Clean up the vault-daytrading checkout or all repos: no. No change.
- (i) ELI-388 portfolio copy: draft. The draft is at `%TEMP%\eli388\draft.md`. A comment on ELI-388 (vault commit 33edff8) points to it and notes the existing unmerged branch `origin/eli-388-sell-ai-setup` (5ae356c, by another fleet agent, 2026-10-20). Nothing ships until Elijah approves the copy and picks one of the two drafts.
- (j) The case collision in batch-run: tag. Merged in plugins PR #9 (merge 017ff50).
  - `slug_for` tags any id with an uppercase letter.
  - `read_record` ignores a record whose `unit_id` differs from the requested id. The Opus audit found that without this, a checkpoint dir written before the change still let `Alpha.json` mark `alpha` done on NTFS.
  - The audit also found an old `Alpha.lock` still blocks `alpha` until its lease expires. SKILL.md and the docstring now say so.
  - Audits: Opus round 1 not clean, Opus round 2 clean, and a Haiku Tier 1 audit of the docstring follow-up clean.
  - Suite: 239 passed, 0 failed on main after the merge.

## Next (in order)
1. Elijah approves or changes the design for (a), below. Nothing gets built before that.
2. Elijah reviews the ELI-388 copy and picks this draft or the 5ae356c branch.
3. Follow-up, not started: `skills/factory/SKILL.md` in claude-config (lines 359-361 and 427-428) exempts "the run's own tickets", plural, and promotes other tickets from Backlog to Todo. CLAUDE.md exempts only the status of the single worked ticket. Found by the PR #20 audit; it predates that PR.
4. Follow-up, not started (handoff 002, Next 3): a hand-made id can equal another id's tagged slug, such as `batch-config.1dd12472`. Reaching it takes typing the hash. Porting the `~/.claude` `load_units` guard (see the (a) design) would close it.
5. Follow-up, not started (handoff 002, Next 4): estimate.py still calls `state._at_least` and `state._retry_while_in_use`.

## Design for (a), waiting on Elijah
The six skills live in both `plugins/` here and `~/.claude/skills/`. Neither plugin is installed: `installed_plugins.json` lists only cloudflare and mattpocock-skills. So the `~/.claude` copies are the ones agents run. No script syncs the two repos.

| Skill | Newer copy | What differs |
|---|---|---|
| batch-run | Both, in different parts | The plugin has pl-1, pl-2 and the case tag. The `~/.claude` copy has NaN and Infinity rejection and a `load_units` guard that refuses ids that would share a file. The plugin lacks both. Both copies sanitize filenames. This list comes from a partial diff and may be incomplete. |
| factory | `~/.claude` | The plugin lacks the blocker ladder (resolver agents, then a blocker ticket) and names no vault path for `ticket.py`. |
| fleet-health | Neither | `~/.claude` hard-codes the owner's home-folder paths; the plugin uses `$HOME`. Both are on purpose. |
| tdd-loop | Neither | `~/.claude` is machine-specific on purpose. |
| handoff | `~/.claude` | Em dashes removed. |
| resume | `~/.claude` | Em dashes removed, new description, plugin-only loop line dropped, root step no longer tied to the handoff skill, and a new paragraph on where handoff docs live. |

Proposed merge directions, whichever repo ends up owning them:
- batch-run: port the `~/.claude`-only hardening into the plugin, then make one copy the source.
- factory: port the blocker ladder into the plugin by hand. The `~/.claude` copy names `ELI-n` and home-folder paths, the plugin stays generic (`<KEY>-<n>`), and the plugins repo is public. Four files differ: SKILL.md and the closing, ledger and team files.
- handoff, resume: port the em dash removal into the plugin, but keep the plugin's clear-instead-of-compact framing, since the plugin is named for it.
- fleet-health and tdd-loop: keep two variants, generic in the plugin and machine-specific in `~/.claude`.

The ownership options are the three from handoff 001 question (a): claude-config owns them, the plugins repo owns them and claude-config installs them, or both keep copies with a sync script.

## Relevant files
- `plugins/autonomous-workflows/skills/batch-run/scripts/state.py`: `slug_for`, `read_record`.
- `plugins/autonomous-workflows/skills/batch-run/SKILL.md`: "The one rule".
- claude-config `CONTEXT.md`, `skills/triage/SKILL.md:19`, `skills/wayfinder/SKILL.md:27`.

## Gotchas
- Do not touch hermes-plugin-claude-subscription-directsdk. Its PR #4 stays open.
- Temp leftovers from earlier sessions are still there: claude-config worktrees `cc-eli-297` and `cc-guardian-doc`, and audit scratch dirs `audit-j` and `audit-j2` under `%TEMP%`.
