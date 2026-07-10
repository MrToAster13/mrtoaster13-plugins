# Stack toolchains

`test`-stage commands per stack. Prefer what the repo configures (a `scripts` entry, a `pyproject` tool, a pre-commit config) over a global tool. Pick the first available in each list; none exist → tell the user, don't invent a checker.

## Node

Package manager from the lockfile: `pnpm-lock.yaml`→`pnpm`, `yarn.lock`→`yarn`, else `npm` (`<pm>` below).

- **Lint/format**: `<pm> run lint` if defined; `<pm> run format:check` or `npx prettier --check .`; `<pm> run typecheck` or `npx tsc --noEmit` when `tsconfig.json` exists.
- **Pre-commit**: `.husky/` fires on `git commit` automatically — don't run by hand. `.pre-commit-config.yaml` → `pre-commit run --all-files`.
- **Tests**: `<pm> test`. A placeholder script (`echo "Error: no test specified"`) means no tests — say so, don't call it a pass.

## Python

Prefer an active/`.venv`/`venv` interpreter, else `PATH`.

- **Lint/format**: `ruff check .` + `ruff format --check .`; else `flake8` + `black --check .` + `isort --check-only .`.
- **Pre-commit**: `.pre-commit-config.yaml` → `pre-commit run --all-files`.
- **Tests**: `pytest` (or `python -m pytest`); else `tox` if `tox.ini`; else `python -m unittest discover`.
