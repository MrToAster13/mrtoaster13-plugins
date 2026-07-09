# autonomous-workflows

Three Claude Code skills for autonomous, evidence-driven work. Each one runs a long loop on its own, but each is built around a hard rule that stops the usual failure mode of "unattended" agents — faking success, over-reaching, or overclaiming.

They run between human gates, not without them. You approve the plan; the agent does the work; you trigger anything that touches the outside world.

## The skills

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

Plugin skills are namespaced: `/autonomous-workflows:remediate`, `/autonomous-workflows:harden-repos`, `/autonomous-workflows:mine-provenance`.

### As standalone skills (bare names)

```bash
# macOS / Linux
mkdir -p ~/.claude/skills
cp -r skills/remediate skills/harden-repos skills/mine-provenance ~/.claude/skills/
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force $HOME/.claude/skills | Out-Null
Copy-Item -Recurse skills/remediate, skills/harden-repos, skills/mine-provenance $HOME/.claude/skills/
```

Restart Claude Code, and you have `/remediate`, `/harden-repos`, `/mine-provenance`.

## The shared philosophy

- **Human gates at the edges.** Approve the plan; trigger the push. The autonomy lives in between.
- **Verify, don't assume.** A fix isn't done until the suite proves it; a claim isn't real until an artifact backs it.
- **Fail honestly.** Stop from a clean checkpoint and say what's stuck, instead of forcing a green or a happy summary.
- **Never push, never overclaim.** Nothing reaches the outside world, or your reputation, without your say-so.

## License

[MIT](../../LICENSE)
