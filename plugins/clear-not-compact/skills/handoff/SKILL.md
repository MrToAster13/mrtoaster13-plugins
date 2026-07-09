---
name: handoff
description: Distill the current session into a durable handoff doc on disk (and optionally a GitHub issue) so you can /clear to a fresh context instead of /compact, then reload it with the resume skill. Use when context is getting long, at the end of a work session, before switching tasks, or whenever the user says "handoff", "save state", "checkpoint", "wrap up", or "instead of compacting".
---

# Handoff

Write the state of this session to disk so a fresh session can pick it up cold. This is the alternative to `/compact`: instead of degrading the current context with a lossy summary, you persist what matters, the user runs `/clear`, and the resume skill reads it back into clean context.

You cannot run `/clear` yourself — a skill can't invoke slash commands. Your job is to write a handoff doc good enough that the next session needs nothing else, then tell the user to clear.

## Steps

1. **Resolve the handoff root once, and use it everywhere.** The handoff folder must live at a stable anchor so the resume skill looks in the same place, no matter which directory either skill runs from.
   - In a git repo: `root="$(git rev-parse --show-toplevel)"`.
   - Not a git repo: anchor to the current working directory, `root="$(pwd)"`, and say so in your closing message.
   - The folder is `"$root/docs/handoffs/"`. Create it if missing. Use this absolute `$root/docs/handoffs/` path in every step below, in the `gh` command, and in the message to the user. Never mix "repo root" prose with a bare `docs/handoffs/` that resolves against the current directory.

2. **Pick the next number, numerically.** Look only at files in `$root/docs/handoffs/` whose names match `^[0-9]+-` (this ignores unrelated files, and ignores the repo's own `docs/example-handoff.md`, which lives outside `docs/handoffs/`). Parse the integer before the first hyphen, take the numeric maximum, add one, and zero-pad to at least three digits — widen past three if you ever pass 999. A lexicographic sort is wrong here (`7-foo.md` would outrank `010-foo.md`), so compare as integers:
   ```bash
   ls "$root/docs/handoffs/" 2>/dev/null | grep -E '^[0-9]+-' | sort -t- -k1,1n | tail -1
   ```
   If none match, start at `001`. Build a short kebab-case slug from the task (e.g. `add-oauth-login`). The filename is `$root/docs/handoffs/NNN-<slug>.md`.

3. **Distill the session** into the template below. Do not transcribe the conversation — extract the decisions and state. Weight two sections heavily, because they are what make the next session smart instead of amnesiac:
   - **Relevant files** — every path that matters and one line on why, so the next session re-reads them instead of re-discovering them.
   - **Key decisions** — choices already made and the reason, so the next session doesn't re-litigate settled questions.
   Be concrete. "Next: wire the callback route" beats "continue the work." Prefer file paths, function names, and commands over prose.

4. **Write the file.** Use this structure, and substitute the real number, slug, title, and status wherever a `NNN`, `<slug>`, `<short title>`, or status placeholder appears — never write the literal placeholders:

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

   Don't stamp a date recalled from memory — you don't reliably know today's date. Git history already timestamps the commit. If the user wants a date in the doc, read it from the system clock (`date`) rather than guessing.

5. **Offer the GitHub issue (optional, ask first).** Probe quietly for all three: a remote (`git remote get-url origin`), `gh` installed, and `gh auth status` logged in. If any of the three fails — not installed, not authed, or no remote — skip this step silently; a missing GitHub setup must never block or error the handoff.
   - If all three hold, ask whether to also file a tracking issue. Filing an issue is outward-facing, so never do it without a yes.
   - On yes, file it from the exact file you wrote in step 4 (reuse that path, don't re-derive the slug):
     ```bash
     gh issue create --title "Handoff NNN: <title>" --body-file "$root/docs/handoffs/NNN-<slug>.md"
     ```
   - Add the returned issue URL to the doc as an `Issue: <url>` line just under the `Status:` line, so the doc still opens with its `# Handoff NNN` heading. The markdown file is always the source of truth; the issue is an index on top of it.

6. **Tell the user what to do next**, plainly, with the real path:
   > Wrote `docs/handoffs/NNN-<slug>.md`. Run `/clear`, then the resume skill in the fresh session to pick up where we left off.

## Notes

- Keep the doc tight. If it's longer than a screen, you're transcribing, not distilling.
- If almost nothing happened this session, say so and suggest the user just keep going rather than handing off — a handoff for a two-message session is wasted ceremony.
- Never invent progress that didn't happen. If a step failed or was skipped, record it under Gotchas exactly as it went.
