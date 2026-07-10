---
name: pre-push
description: Pre-push orchestrator — detect Python/Node stack, then run simplify, review, security, test, commit as gated stages (retry flaky network, chmod +x scripts, stop on real failures). Use when ready to push to main, or on "pre-push", "clean up before pushing", "polish and commit", "finalize", "ship it".
---

# Pre-Push

Everything you run before pushing to main, once you like the result. Each stage is a **gate**: it passes quietly, or trips on a genuine failure and stops for a fix. Never pushes — stops at the commit and hands back.

## Setup

- Resolve the **target repo** root: `git rev-parse --show-toplevel`. Fails → not a git repo, stop. Run every stage from this path.
- `git -C <root> status --short` empty → nothing to ship, stop.
- Polish-and-ship, not build. Don't change behavior or add features.

## Detect the stack

- **Node**: `package.json` exists.
- **Python**: any of `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements*.txt`, `Pipfile`, `tox.ini`.

Both can be true — run both toolchains. Neither → skip stack commands, ask the user. Concrete commands per stack: [`stacks.md`](stacks.md).

## Gate: fixing what surfaces

A gate trips only on a **genuine failure** — a security defect, a failing test, a functional bug — never a style nit or theoretical note.

1. Show the user the problem, location, and error/repro. Ask: `diagnosing-bugs`, fix another way, or skip? Wait.
2. On go, invoke `diagnosing-bugs` with the specifics.
3. Re-run the tripped stage, then re-run `test`. Report pass/fail counts.
4. If it won't resolve in a reasonable pass, stop and hand back. Never commit over a blocker.

Style nits: fix directly or leave; don't pause for them.

## Stages, in order

Run each from the repo root; let it finish before the next.

1. **simplify** — invoke `simplify`.
2. **review** — invoke `code-review` (Standards + Spec; reports, doesn't fix). Functional bug → gate.
3. **security** — `cd <root>` first, else `security-review` silently narrows scope; then invoke it. Real issue → gate.
4. **test** — from `stacks.md`: lint/format check, pre-commit hooks if configured, then the full suite. Report pass/fail. Genuine failure → gate. **Retry flaky network:** a run dying on `ETIMEDOUT`/`ECONNRESET`/`ENOTFOUND`/`EAI_AGAIN`/`getaddrinfo`/`Could not resolve host`/`Read timed out`/`Retrying`/registry `50[234]` is infra, not a test failure — re-run up to 3×. Any other non-zero exit is genuine; take it to the gate, don't retry. 3 network failures → stop, tell the user, don't commit on unrun tests.
5. **commit**:
   - **chmod +x scripts.** A push ships the whole tree, so any tracked `.sh`-or-shebang file at mode `100644` breaks a Linux install. `git ls-files`, and for each script still `100644`: `git update-index --chmod=+x <path>`. Report which you flipped.
   - **Scan for secrets** in changed/staged files (SSH logs, credentials, tokens). Suspicious → stop.
   - Invoke `commit`, or inline: match `git log --oneline -20` convention; commit staged-only if anything's staged else `git add -A`; message states intent, not a file list; `git commit` with native hooks — never `--no-verify`/`--no-gpg-sign`/amend.

## Done

Summarize: stack, `simplify` changes, review findings, debug-loop fixes, scripts flipped, test counts (and retries), commit hash. State it's committed, ready to push, and **not** pushed.
