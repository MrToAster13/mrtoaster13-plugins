# clear-not-compact

> **Deprecated. No longer maintained.**
>
> This plugin solved a problem that has since been solved better upstream. Claude
> Code now auto-summarizes context on its own and tells the model it does not need
> to wrap up early or hand off mid-task, and Opus 5 ships a 1M-token window. The
> manual persist-then-`/clear` loop below buys little on top of that, and it costs
> you a round trip every time context fills.
>
> The repo stays published so existing installs keep working. Nothing here is
> being updated. If you want the idea, take the handoff doc template under
> "What a handoff captures" and write one by hand when you actually need it,
> usually on a long multi-day build where you want a durable record on disk
> rather than a summary in a window.
>
> Everything below this line is the original README, kept for reference.

---

Two Claude Code skills that replace `/compact` with a cleaner habit: write the
session's state to disk, `/clear` to an empty context, and read the state back.

`/compact` keeps you in the same session and swaps your real history for a lossy
summary. You then keep working on top of that summary, and quality drifts as the
model reasons over a paraphrase of a paraphrase. Clearing avoids that. You never
work in a degraded context, because every session starts fresh from a doc you
wrote on purpose.

- **`/handoff`** — distills the current session into a numbered doc under
  `docs/handoffs/`, and optionally files a GitHub issue. Then tells you to clear.
- **`/resume`** — finds the latest handoff, reads it plus the files it points to,
  restates the plan, and waits for your go-ahead.

## The loop

```
/handoff        # session 1: persist goal, decisions, next steps, key files
/clear          # empty the context
/resume         # session 2: rebuild context from the doc, confirm, continue
```

Repeat whenever context fills up. Each handoff is numbered, so `docs/handoffs/`
becomes a log of how the work evolved — what got done, what changed, what carried
forward.

## What a handoff captures

```markdown
# Handoff NNN: <short title>

Status: in-progress | blocked | ready-to-verify

## Goal
What we're building and why.

## Done
- Concrete things completed this session.

## Next (in order)
1. The immediate next step.

## Key decisions
- <choice> — because <reason>.

## Relevant files
- `path/to/file` — why it matters.

## Gotchas / open questions
- Traps, blockers, things to watch.
```

The **Relevant files** and **Key decisions** sections are the ones that make the
next session smart instead of amnesiac. `/resume` re-reads the listed files, so
you get the actual code back — not a summary of it — and it won't re-litigate a
question you already settled. See [`docs/example-handoff.md`](docs/example-handoff.md)
for a filled-out one.

## Install

### As standalone skills (bare `/handoff` and `/resume`)

Copy the two skill folders into your personal skills directory:

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
cp -r skills/handoff skills/resume ~/.claude/skills/
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force $HOME/.claude/skills | Out-Null
Copy-Item -Recurse skills/handoff, skills/resume $HOME/.claude/skills/
```

Restart Claude Code. You now have `/handoff` and `/resume`.

### As a plugin (managed install, updates)

```
/plugin marketplace add MrToAster13/mrtoaster13-plugins
/plugin install clear-not-compact@mrtoaster13-plugins
```

Plugin skills are namespaced, so you invoke them as
`/clear-not-compact:handoff` and `/clear-not-compact:resume`. If you'd rather
have the bare names, use the standalone install above instead.

## Docs

- [`CONTEXT.md`](CONTEXT.md) — the vocabulary: handoff, resume, distill, clear vs compaction.
- [`docs/adr/`](docs/adr/) — why it's built this way:
  - [0001 — clear-and-resume, not compaction](docs/adr/0001-clear-and-resume-not-compaction.md)
  - [0002 — markdown canonical, issue optional](docs/adr/0002-markdown-canonical-issue-optional.md)
  - [0003 — distribute as both plugin and standalone](docs/adr/0003-distribute-as-plugin-and-standalone.md)

## When you actually need this

Mostly on models that degrade through compaction. Anthropic's
[harness-design writeup](https://www.anthropic.com/engineering/harness-design-long-running-apps)
describes "context anxiety" — the model rushing to finish as its window fills,
with quality dropping. It's pronounced on Sonnet and largely absent on recent
Opus, where you can lean on `/compact` more. Reach for this loop on long,
multi-day builds and on models where compaction visibly hurts. If you're only a
third into your context window, you don't need it yet — check with `/context`.

## Credit

The workflow comes from a
[r/ClaudeCode thread](https://www.reddit.com/r/ClaudeCode/comments/1ti1kit/is_compact_good_now/)
where people compared `/compact` to "cooking with alzheimer" and found that
persist-then-`/clear` gave better results than daisy-chaining compactions through
one long session. This repo packages the two load-bearing steps of that loop.

## License

[MIT](LICENSE)
