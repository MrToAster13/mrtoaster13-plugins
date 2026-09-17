# mrtoaster13-plugins

Claude Code workflow plugins by [MrToAster13](https://github.com/MrToAster13). One marketplace, installable whole or piece by piece.

## Plugins

### [`clear-not-compact`](plugins/clear-not-compact/) (deprecated)

**No longer maintained.** Built-in auto-compaction and a 1M-token context window
removed the problem this solved. Still installable so existing installs keep
working. See [its README](plugins/clear-not-compact/README.md) for the why.

### [`autonomous-workflows`](plugins/autonomous-workflows/) — autonomous, evidence-driven work

Seven skills that run bounded loops on their own, each built around a hard rule that stops the usual "unattended agent" failure modes:

- **`/factory`** — turns an approved spec into an MVP unattended: ten agents at once in isolated worktrees, an adversary pass on every diff, blockers parked instead of stopping the run, and one PR left for you to merge.
- **`/remediate`** — a test-verified fix loop that iterates until green but can't cheat: it never edits tests, and stops honestly when a fix is out of reach.
- **`/harden-repos`** — fans out one fresh-context agent per repo, applies only verified-safe fixes on a branch, never pushes, and aggregates into `HARDENING_HANDOFF.md`.
- **`/mine-provenance`** — audits a portfolio against ground-truth artifacts (commit/file/test), sorts claims into VERIFIED / gaps / provenance-flags, and never overclaims.
- **`/fleet-health`** — audits every local repo against its live remote (unpushed commits, stale merged branches, staged secrets), prints the shell-correct ship command, and runs it only after a yes.
- **`/tdd-loop`** — red, then a green loop capped at 10 logged iterations, then an adversarial review that hunts the bugs the tests missed; never edits an assertion, never pushes.
- **`/batch-run`** — writer plus independent verifier over N work units, each checkpointed to disk as it lands, so a killed session loses nothing finished.

## Install

Add the marketplace once, then install whichever plugins you want:

```
/plugin marketplace add MrToAster13/mrtoaster13-plugins
/plugin install clear-not-compact@mrtoaster13-plugins
/plugin install autonomous-workflows@mrtoaster13-plugins
```

Plugin skills are namespaced (`/clear-not-compact:handoff`, `/autonomous-workflows:remediate`). For bare names, each plugin's README has a standalone-install path that copies its skill folders into `~/.claude/skills/`.

## Layout

```
mrtoaster13-plugins/
├── .claude-plugin/
│   └── marketplace.json        # lists the plugins below
└── plugins/
    ├── clear-not-compact/       # /handoff, /resume
    └── autonomous-workflows/    # /factory, /remediate, /harden-repos, /mine-provenance,
                                 # /fleet-health, /tdd-loop, /batch-run
```

Each plugin owns its `.claude-plugin/plugin.json`, `skills/`, and README, so it versions and installs on its own.

## License

[MIT](LICENSE)
