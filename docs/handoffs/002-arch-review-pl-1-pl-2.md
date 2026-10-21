# Handoff 002: architecture fixes pl-1 and pl-2

Status: pl-1 in review on branch arch/pl-1-model-policy. pl-2 not started.

## Goal
Elijah approved two fixes from the 2026-10-21 architecture review (`C:\Users\Elijah\AppData\Local\Temp\architecture-review-20261021-012113.html`, cards pl-1 and pl-2; notes in `C:\Users\Elijah\AppData\Local\Temp\arch-review\plugins-linear.md`). Both are in `plugins/autonomous-workflows/skills/batch-run/`. Each fix gets one commit, the full suite, an adversarial audit loop, then push and merge.

## Done
- pl-1 is commit 409a644 on arch/pl-1-model-policy. It moves the model and effort policy into `scripts/estimate.py` alone.
  - `estimate.py` gains `DEFAULT_CHOICE`, `default_config()`, `load_config(dir, or_default=False)` and the CLI flag `load-config --or-default`. With the flag, a missing or unreadable record prints the default record (Haiku writer, Opus verifier) and writes nothing.
  - `workflows/batch.js` lost its copy of the rules: name normalising, fallbacks, effort drops, warnOnce logging and the parity comment. When any unit is pending, a guard refuses to start unless each stage names exactly opus or haiku, and any effort is plain lowercase letters on an opus stage. It passes the stage settings through unchanged.
  - `SKILL.md` step 4 now says to run `load-config --dir <checkpoint> --or-default` and pass its worker and verifier as `models`.
  - The parity tests are gone. New tests cover the guard and run the output of `load-config --or-default` through a whole batch.js run.
  - The suite went from 226 passed (baseline on main 1c20624) to 202 passed, 0 failed. The parity cases account for the drop.
  - Opus audit round 1 found two should-fix items: batch.js passed any effort string through, and the start log could disagree with the call. It also found four wording nits. Round 2 found four wording nits. Round 3 found one, a test name that covered only the model cases. All were fixed in the amended commit.

## Next (in order)
1. Finish the pl-1 audit loop, push, open the PR, merge, and link the PR.
2. pl-2: make `scripts/state.py` own `batch-config.json`. Add `config_path`, `read_config` and `write_config` to state.py, and have estimate.py's `save_config` and `load_config` call them instead of `state._atomic_write_json`, `state._read_json` and `CONFIG_NAME`. Reserve the config name in `slug_for` (see Gotchas). Then run the suite, audit, merge, and update this handoff.

## Key decisions
- batch.js does not check which effort levels are valid. That list lives in estimate.py only. A hand-built `{"model": "opus", "effort": "banana"}` reaches agent(). SKILL.md says the models must come from load-config.
- The canonical batch-run copy is this repo's `plugins/autonomous-workflows/skills/batch-run`. A second, older copy lives at `C:\Users\Elijah\.claude\skills\batch-run` in claude-config (last commit 8949d21, 2026-10-19). It still has the built-in batch.js defaults and no `--or-default`, and this work did not change it. That copy is self-contained, so nothing in it breaks. Syncing it waits on Elijah's ownership answer, question (a) in handoff 001.

## Relevant files
- `plugins/autonomous-workflows/skills/batch-run/scripts/estimate.py`: `load_config`, `default_config`, the CLI.
- `plugins/autonomous-workflows/skills/batch-run/workflows/batch.js`: `refusal()` and `stageOpts`.
- `plugins/autonomous-workflows/skills/batch-run/tests/test_batch_js.py`, `tests/test_estimate.py`.
- `plugins/autonomous-workflows/skills/batch-run/scripts/state.py`: `slug_for`, `record_path` and the private helpers estimate.py calls today.

## Gotchas / open questions
- The pl-2 deletion test holds. `state.slug_for("batch-config")` returns `batch-config`, so a unit with that id writes its record to `checkpoint/batch-config.json` and overwrites the saved model choice, and the reverse. On Windows, a case variant such as `Batch-Config` collides too. Only state.py can prevent that, because it names the record files.
- Leave the untracked `fuzz9.out` in the repo root alone. Its deletion waits on Elijah, question (d) in handoff 001.
- Elijah's answers to questions (a) through (f) in handoff 001, and to (g) and (h) asked in chat after it, are still pending.
