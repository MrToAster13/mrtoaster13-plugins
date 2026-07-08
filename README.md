# clear-not-compact

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
cp -r skills/handoff skills/resume ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills/handoff, skills/resume $HOME/.claude/skills/
```

Restart Claude Code. You now have `/handoff` and `/resume`.

### As a plugin (managed install, updates)

```
/plugin marketplace add MrToAster13/clear-not-compact
/plugin install clear-not-compact@mrtoaster13-plugins
```

Plugin skills are namespaced, so you invoke them as
`/clear-not-compact:handoff` and `/clear-not-compact:resume`. If you'd rather
have the bare names, use the standalone install above instead.

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
