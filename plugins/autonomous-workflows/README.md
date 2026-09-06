# autonomous-workflows

Four Claude Code skills for autonomous, evidence-driven work. Each one runs a long loop on its own, but each is built around a hard rule that stops the usual failure mode of "unattended" agents — faking success, over-reaching, or overclaiming.

They run between human gates, not without them. You approve the plan; the agent does the work; you trigger anything that touches the outside world.

## The skills

### `/factory` — the unattended ticket loop

Takes an approved spec and builds it into a working MVP without you in the room. It runs `to-spec → to-tickets → implement → code-review` on a loop, orchestrating from your main session so the context never fills: the orchestrator holds only a ledger and one line summaries, and every piece of real work happens in a subagent whose context dies with it.

The rule that makes it usable: **a blocker parks the ticket, it does not stop the run.** Anything reversible in an afternoon gets decided and logged. Anything that would waste work or ship something you did not agree to gets parked as a question, and everything not downstream of it keeps building. You are interrupted once, at the end, with every open question at the same time.

Ten agents run at once, counted across the whole tree. A wider frontier queues and feeds in the moment a slot frees, so the cap slows the run down instead of serialising it by design. Each ticket gets its own git worktree, its own branch, and an adversary pass on its diff before anything merges. Everything lands on one integration branch, and exactly one pull request is left for you to merge.

The target is a prototype that reads like a polished v3 and scopes like a v0.1. `factory-polish` enforces that bar at the end and may not add a feature to reach it.

### `/remediate` — test-verified fix loop

Reads `REMEDIATION_PLAN.md` (or drafts one and waits for your OK), then implements each phase, runs the full suite, and iterates until green.

The rule that makes it safe: **green must come from fixing code, never from weakening tests.** It never edits test files, and it snapshots passed/skipped/total before and after — so it can't quietly `.skip` a failing test to force a pass. Environmental errors (DNS, timeouts, `429`) retry with backoff; a failing assertion is always treated as a real bug. Caps at 3 attempts per phase, then stops from a clean checkpoint and tells you exactly what's stuck. Commits each green phase to a `remediate/<date>` branch and hands you a ready-to-run push + PR — it never pushes itself.

### `/harden-repos` — parallel multi-repo cleanup

Fans out one fresh-context sub-agent per repo, so no single context has to hold the whole portfolio — which is what makes long multi-repo passes fail halfway. Each agent secret-scans, reviews, and applies only **verified-safe** fixes on a `harden/<date>` branch: mechanical changes (formatting, pre-commit config, `.gitignore`) always; behavioral changes only if the suite proves they didn't break anything. No tests means mechanical-only. A secret finding is a hard stop for that repo. Nothing is ever pushed. Everything aggregates into `HARDENING_HANDOFF.md`.

Targets come from `~/.harden-targets` (your maintained list, kept out of any shared repo) or from arguments.

### `/mine-provenance` — evidence-backed portfolio audit

Audits a skills/portfolio doc against ground truth and extends it. Every claim is tied to a **commit, file, or test** — or it doesn't count as verified. Results sort into three buckets:

- **VERIFIED** — backed by a cited artifact.
- **`GAPS.md`** — claimed, but no artifact found. Needs verification, not rejection.
- **`PROVENANCE.md`** — evidence shows the work is forked or derived, recorded plainly so the portfolio never presents derived work as originated.

Transcripts are used only as discovery leads — never as proof. And it never argues with your claims; it sorts them and lets the evidence speak.

## Install

### As a plugin (managed, updates)

```
/plugin marketplace add MrToAster13/mrtoaster13-plugins
/plugin install autonomous-workflows@mrtoaster13-plugins
```

Plugin skills are namespaced: `/autonomous-workflows:factory`, `/autonomous-workflows:remediate`, `/autonomous-workflows:harden-repos`, `/autonomous-workflows:mine-provenance`.

### As standalone skills (bare names)

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
cp -r skills/factory skills/remediate skills/harden-repos skills/mine-provenance ~/.claude/skills/
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force $HOME/.claude/skills | Out-Null
Copy-Item -Recurse skills/factory, skills/remediate, skills/harden-repos, skills/mine-provenance $HOME/.claude/skills/
```

Restart Claude Code, and you have `/factory`, `/remediate`, `/harden-repos`, `/mine-provenance`.

## Archive

[`archive/`](archive/) holds skills that were part of this plugin and no longer
are. Nothing in there loads. `pre-push` lives there now: its stack detection went
stale, and the work it gated is covered by `/factory` and `/remediate`.

## The shared philosophy

- **Human gates at the edges.** Approve the plan; trigger the push. The autonomy lives in between.
- **Verify, don't assume.** A fix isn't done until the suite proves it; a claim isn't real until an artifact backs it.
- **Fail honestly.** Stop from a clean checkpoint and say what's stuck, instead of forcing a green or a happy summary.
- **Never push, never overclaim.** Nothing reaches the outside world, or your reputation, without your say-so.

## License

[MIT](../../LICENSE)
