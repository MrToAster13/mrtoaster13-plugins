# Handoff 002: architecture fixes pl-1 and pl-2

Status: done. pl-1 merged through PR #6 (merge 3b94133) and pl-2 through PR #7 (merge 0d691c1). The review has no other pl-* cards for this repo, so the pl-* work is done. The follow-ups under Next and the open questions under Gotchas remain.

## Goal
Elijah approved two fixes from the 2026-10-21 architecture review (`%TEMP%\architecture-review-20261021-012113.html`, cards pl-1 and pl-2; notes in `%TEMP%\arch-review\plugins-linear.md`). Both are in `plugins/autonomous-workflows/skills/batch-run/`. Each fix gets one commit, the full suite, an adversarial audit loop, then push and merge.

## Done
- pl-1 is commit 409a644, merged through PR #6 as 3b94133. It moves the model and effort policy into `scripts/estimate.py` alone.
  - `estimate.py` gains `DEFAULT_CHOICE`, `default_config()`, `load_config(dir, or_default=False)` and the CLI flag `load-config --or-default`. With the flag, a missing or unreadable record prints the default record (Haiku writer, Opus verifier) and writes nothing.
  - `workflows/batch.js` lost its copy of the rules: name normalising, fallbacks, effort drops, warnOnce logging and the parity comment. When any unit is pending, a guard refuses to start unless each stage names exactly opus or haiku, and any effort is plain lowercase letters on an opus stage. It passes the stage settings through unchanged.
  - `SKILL.md` step 4 now says to run `load-config --dir <checkpoint> --or-default` and pass its worker and verifier as `models`.
  - The parity tests are gone. New tests cover the guard and run the output of `load-config --or-default` through a whole batch.js run.
  - The suite went from 226 passed (baseline on main 1c20624) to 202 passed, 0 failed. The parity cases account for the drop.
  - Opus audit round 1 found two should-fix items: batch.js passed any effort string through, and the start log could disagree with the call. It also found four wording nits. Round 2 found four wording nits. Round 3 found one, a test name that covered only the model cases. All were fixed in the amended commit.
- pl-2 is commit afbdd4d, merged through PR #7 as 0d691c1. `scripts/state.py` now owns `batch-config.json`.
  - state.py gains `CONFIG_NAME`, `config_path`, `read_config` and `write_config`. estimate.py's `save_config` and `load_config` call them, and estimate.py no longer calls `state._read_json` or `state._atomic_write_json` or keeps its own copy of the file name.
  - `slug_for` hash-tags any id whose slug equals `batch-config` in any case, so a unit record can no longer replace the saved model choice, or the reverse. A unit with such an id that was recorded before this change is pending again and gets redone. Its lock file moves the same way.
  - New tests go through the new interface. The suite went from 202 passed to 215 passed, 0 failed.
  - Opus audit round 1 found no must-fix items. One should-fix and one nit were pre-existing and are listed below as follow-ups. The other nit, the lock file move, went into the commit message. Round 2 found one nit: the old `slug_for` docstring line "two different ids can never land on one file" was false on NTFS. The docstring now names the case collision.

## Next (in order)
1. Done: git shows PR #7 merged as 0d691c1.
2. Done in PR #9 (merge 017ff50), which does both fixes below. Handoff 003 has the details. Original note: unit ids that differ only in case, such as `Alpha` and `alpha`, share one record file on NTFS. A probe recorded `Alpha` as verified, then ran units `[alpha, Alpha]`: `todo` returned nothing and `report` counted 2 verified, though `alpha` never ran. The `slug_for` docstring now says so. One fix tags any slug with an uppercase letter. That changes the slug of every such id, so their finished units would be redone. Another fix has `read_record` check the stored `unit_id`. This needs a decision before anyone builds it.
3. Follow-up, not started: a tagged slug can equal another id's plain slug. `slug_for("batch-config.1dd12472")` equals `slug_for("batch-config")`, and `"a_b.<hash>"` can equal the tag of `"a b"`. Both ids would have to be in one units.json.
4. Follow-up, not started: estimate.py still calls `state._at_least` and `state._retry_while_in_use`. Neither touches the config file, so pl-2 left them alone.

## Key decisions
- batch.js does not check which effort levels are valid. That list lives in estimate.py only. A hand-built `{"model": "opus", "effort": "banana"}` reaches agent(). SKILL.md says the models must come from load-config.
- This work changed only this repo's `plugins/autonomous-workflows/skills/batch-run`. A second copy lives at `~/.claude/skills/batch-run` in claude-config (last commit 8949d21, 2026-10-19). It still has the built-in batch.js defaults, no `--or-default` and the old config handling, and this work did not change it. That copy is self-contained, so nothing in it breaks, and nothing in this repo imports from it. Syncing it waits on Elijah's ownership answer, question (a) in handoff 001. Neither copy is canonical: they diverged both ways, and the `~/.claude` copy has hardening the plugin lacks. See the (a) design in handoff 003.
- pl-2 fixed only the collision with the config file and left the case collision between two unit ids (Next item 2) alone. Fixing that one redoes finished work, which goes beyond pl-2.

## Relevant files
- `plugins/autonomous-workflows/skills/batch-run/scripts/estimate.py`: `load_config`, `save_config`, `default_config`, the CLI.
- `plugins/autonomous-workflows/skills/batch-run/scripts/state.py`: `slug_for`, `config_path`, `read_config`, `write_config`.
- `plugins/autonomous-workflows/skills/batch-run/workflows/batch.js`: `refusal()` and `stageOpts`.
- `plugins/autonomous-workflows/skills/batch-run/tests/test_state.py`, `tests/test_estimate.py`, `tests/test_batch_js.py`.

## Gotchas / open questions
- Leave the untracked `fuzz9.out` in the repo root alone. Its deletion waits on Elijah, question (d) in handoff 001. Later deleted after a backup; see handoff 003 (d).
- Elijah's answers to questions (a) through (f) in handoff 001, and to (g) and (h) asked in chat after it, were pending when this was written. Handoff 003 records the answers.
- A pytest parametrize value of 100,000 bytes becomes the test id, and here that test errored before it ran. Short `ids` fixed it.
