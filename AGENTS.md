# AGENTS.md

Public Claude Code plugin marketplace. Two plugins live under `plugins/`:
`autonomous-workflows` and the deprecated `clear-not-compact`.
`.claude-plugin/marketplace.json` lists them, and each plugin has its own
`.claude-plugin/plugin.json`, `skills/` and README.

## Setup

Python 3 with pytest (CI runs 3.12; older versions are untested), and node 22 for the
batch.js tests:

```
python -m venv .venv
.venv/bin/python -m pip install pytest
```

## Tests

Run the full suite from the repo root:

```
python -m pytest -q
```

It collects the tests under `plugins/autonomous-workflows/skills/batch-run/tests/`,
`plugins/autonomous-workflows/skills/factory/tests/` and `tests/`. `tests/test_public_hygiene.py`
fails when a tracked file names a private path or host. `test_batch_js.py`
skips without `node` on PATH, so a run without node is not a full baseline.
CI runs the same command in `.github/workflows/tests.yml`.

## Shared skills

Some skills here are generated from the private claude-config repo. The files
listed in `.shared-skills.json` must not be edited here. Change them in
claude-config `skills/` and run its `bin/sync-shared-skills.py`. The
`shared-skills` workflow runs `.github/check-shared-skills.py` and fails on a
hand edit.

## Merging

Work on a branch and open a pull request. Merge once both workflows are green
on the latest commit. Never force-push. Nothing here may name private paths,
hosts or secrets, because the repo is public.
