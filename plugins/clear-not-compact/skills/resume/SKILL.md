---
name: resume
description: Read the latest handoff doc back into a fresh session so you can continue work after /clear instead of /compact. Use at the start of a new session, after the user runs /clear, or when the user says "resume", "pick up where we left off", "continue from the handoff", or names a handoff file or issue to load.
---

# Resume

Load a handoff doc into this fresh session and re-establish the plan. This is the second half of the clear-instead-of-compact loop: the handoff skill wrote the state, the user ran `/clear`, and now you rebuild context from disk — cleanly, without the cruft of the old session.

## Steps

1. **Find the handoff.**
   - **Resolve the same root the handoff skill used**, so you read from where it wrote: in a git repo `root="$(git rev-parse --show-toplevel)"`, otherwise `root="$(pwd)"`. The handoff folder is `"$root/docs/handoffs/"`.
   - **If the user pointed at something specific, honor it.** Treat a bare number, a slug, or an `*.md` name as a handoff *file* under `$root/docs/handoffs/`. Only treat it as a GitHub issue when they clearly mean one — the word "issue", a `#N`, or a GitHub URL — and only then, after confirming `gh` is installed and authed (`gh auth status`); read it with `gh issue view <n>`. If `gh` isn't available, fall back to the local file it links to.
   - **Otherwise take the latest by number.** Consider only names matching `^[0-9]+-`, parse the leading integer, and take the numeric maximum — not a lexicographic sort, which would rank `7-foo.md` above `010-foo.md`:
     ```bash
     ls "$root/docs/handoffs/" 2>/dev/null | grep -E '^[0-9]+-' | sort -t- -k1,1n | tail -1
     ```
   - **If nothing matches** (`$root/docs/handoffs/` is missing, or holds no `NNN-*.md` — a lone `.gitkeep` doesn't count), say so and ask what to resume from. Don't guess or invent state.

2. **Read it in full, then load its files.** Read the handoff, then read each path under **Relevant files** — pull them into context now, so you're working from the actual code and not a summary of it. This is what makes resume better than a compaction summary: you get the real files back, not a lossy paraphrase.
   - Don't halt on a missing path. Renamed, moved, and deleted files are exactly the stale-plan case this workflow exists to survive. Collect any that no longer exist, and surface them in the restate ("these listed files are now missing: X, Y — the plan may be stale"). If a large fraction are gone, treat it as a signal to re-confirm the plan with the user before doing anything.

3. **Restate, briefly**, so the user can confirm you loaded the right state:
   - The **goal** (one line).
   - The **status** from the doc (in-progress / blocked / ready-to-verify) — and flag it if it's `blocked` or `ready-to-verify`, since that changes what happens next.
   - What's **done**, and the **immediate next step** you're about to take.

4. **Stop.** Do not start implementing yet. Wait for the user to confirm or redirect. The handoff captured a plan; the user may have changed their mind between sessions, and charging ahead on a stale plan is exactly the failure mode this workflow exists to avoid.

## Notes

- If the handoff references an open question or blocker under Gotchas, raise it now — it may be the first thing to resolve.
- If several handoffs describe the same task across sessions, the highest-numbered one is authoritative, but skim the prior one or two for decisions that still apply.
- Once the user confirms, proceed with the plan. When that chunk of work is done and context is filling up again, run the handoff skill again (`/handoff`, or `/clear-not-compact:handoff` when it's installed as a plugin) and repeat the loop.
