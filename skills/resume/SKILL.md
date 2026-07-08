---
name: resume
description: Read the latest handoff doc back into a fresh session so you can continue work after /clear instead of /compact. Use at the start of a new session, after the user runs /clear, or when the user says "resume", "pick up where we left off", "continue from the handoff", or names a handoff file or issue to load.
---

# Resume

Load a handoff doc into this fresh session and re-establish the plan. This is the second half of the clear-instead-of-compact loop: `/handoff` wrote the state, the user ran `/clear`, and now you rebuild context from disk — cleanly, without the cruft of the old session.

## Steps

1. **Find the handoff doc.**
   - If the user named a specific file or a GitHub issue, use that. For an issue: `gh issue view <n>`.
   - Otherwise, list `docs/handoffs/NNN-*.md` and take the **highest** `NNN` — that's the most recent handoff.
   - If `docs/handoffs/` doesn't exist or is empty, say so and ask what to resume from. Don't guess or invent state.

2. **Read it in full.** Then read the files it lists under **Relevant files** — pull them into context now, so you're working from the actual code and not just the summary of it. This is the step that makes resume better than a compaction summary: you get the real files back, not a lossy paraphrase.

3. **Restate, briefly**, so the user can confirm you loaded the right state:
   - The **goal** (one line).
   - What's **done**.
   - The **immediate next step** you're about to take.

4. **Stop.** Do not start implementing yet. Wait for the user to confirm or redirect. The handoff captured a plan; the user may have changed their mind between sessions, and charging ahead on a stale plan is exactly the failure mode this workflow exists to avoid.

## Notes

- If the handoff doc references an open question or blocker under Gotchas, raise it now — it may be the first thing to resolve.
- If several handoff docs describe the same task across sessions, the highest-numbered one is authoritative, but skim the prior one or two for decisions that still apply.
- Once the user confirms, proceed with the plan. When that chunk of work is done and context is filling up again, run `/handoff` and repeat the loop.
