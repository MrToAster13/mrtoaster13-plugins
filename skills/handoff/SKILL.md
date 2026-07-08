---
name: handoff
description: Distill the current session into a durable handoff doc on disk (and optionally a GitHub issue) so you can /clear to a fresh context instead of /compact. Use when context is getting long, at the end of a work session, before switching tasks, or whenever the user says "handoff", "save state", "wrap up", or "instead of compacting".
---

# Handoff

Write the state of this session to disk so a fresh session can pick it up cold. This is the alternative to `/compact`: instead of degrading the current context with a lossy summary, you persist what matters, the user runs `/clear`, and `/resume` reads it back into clean context.

You cannot run `/clear` yourself — a skill can't invoke slash commands. Your job is to write a handoff doc good enough that the next session needs nothing else, then tell the user to clear.

## Steps

1. **Locate the handoff folder.** Use `docs/handoffs/` at the repo root. If it doesn't exist, create it. If this isn't a git repo, use `docs/handoffs/` relative to the current working directory anyway — a plain folder is fine.

2. **Pick the next number.** List existing `docs/handoffs/NNN-*.md` files and take the highest `NNN`, then add one, zero-padded to three digits (`001`, `002`, ...). If none exist, start at `001`. Build a short kebab-case slug from the task (e.g. `add-oauth-login`). The filename is `NNN-<slug>.md`.

3. **Distill the session** into the template below. Do not transcribe the conversation — extract the decisions and state. Weight two sections heavily, because they are what make the next session smart instead of amnesiac:
   - **Relevant files** — every path that matters and one line on why, so the next session re-reads them instead of re-discovering them.
   - **Key decisions** — choices already made and the reason, so the next session doesn't re-litigate settled questions.
   Be concrete. "Next: wire the callback route" beats "continue the work." Prefer file paths, function names, and commands over prose.

4. **Write the file** using this exact structure:

   ```markdown
   # Handoff NNN: <short title>

   Status: in-progress | blocked | ready-to-verify

   ## Goal
   What we're building and why. One paragraph.

   ## Done
   - Concrete things completed this session.

   ## Next (in order)
   1. The immediate next step — specific enough to start without thinking.
   2. Then this.

   ## Key decisions
   - <choice> — because <reason>.

   ## Relevant files
   - `path/to/file` — why it matters / what's in it.

   ## Gotchas / open questions
   - Traps, blockers, half-finished edits, things to watch.
   ```

   Do not stamp a date from memory — you don't reliably know today's date. If the user wants a date, ask, or leave it out. Git history already timestamps the commit.

5. **Offer the GitHub issue (optional, ask first).** Only if the repo has a GitHub remote and `gh` is authenticated:
   - Check quietly: `git remote get-url origin` succeeds and `gh auth status` is logged in.
   - If both hold, ask the user whether to also file a tracking issue. Filing an issue is outward-facing, so never do it without a yes.
   - On yes: `gh issue create --title "Handoff NNN: <title>" --body-file docs/handoffs/NNN-<slug>.md`. Add the returned issue URL as the first line of the handoff doc so `/resume` and future readers can find it.
   - The markdown file is always the source of truth. The issue is an index on top of it. If there's no remote or `gh` isn't authed, skip this silently — never let a missing GitHub setup block the handoff.

6. **Tell the user what to do next**, plainly:
   > Wrote `docs/handoffs/NNN-<slug>.md`. Run `/clear`, then `/resume` in the fresh session to pick up where we left off.

## Notes

- Keep the doc tight. If it's longer than a screen, you're transcribing, not distilling.
- If almost nothing happened this session, say so and suggest the user just keep going rather than handing off — a handoff for a two-message session is wasted ceremony.
- Never invent progress that didn't happen. If a step failed or was skipped, record it under Gotchas exactly as it went.
