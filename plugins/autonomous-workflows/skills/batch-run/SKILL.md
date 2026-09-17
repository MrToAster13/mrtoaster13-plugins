---
name: batch-run
description: Run a batch of similar work units through a writer agent and an independent verifier agent in parallel, checkpointing each unit to disk the moment it lands, so a session that dies on limits loses no finished work and resumes without reprocessing anything. Use for "batch run", "generate these N documents", "run this over every item", "resume the batch", or any fan-out where losing the run must not lose the output.
---

# Batch run

Produce N artifacts in parallel, check each one with a second agent that never
saw the first agent's reasoning, and record the outcome per unit as it happens.
Then a killed session costs you the units still in flight, not the batch.

## The one rule

A unit is done if and only if `checkpoint/<slug>.json` exists, parses, and says
`status: verified`. Missing, malformed, `written`, `failed` all mean pending.

Nothing else defines completion. Not the workflow's return value, not the
artifacts on disk, not what an agent said in its summary. When you report
status to the user, read it from `state.py report` and nowhere else.

This is also why there is no error handling for session limits. A session that
dies on limits does not get to run cleanup code, so any design that recovers via
a catch block loses exactly the runs it was built for. Here, failure is the
absence of a success record, which needs no handler.

## Inputs

**`units.json`** - a list. Each entry needs `id`; everything else is payload the
worker prompt can interpolate.

```json
[{"id": "acme-secops-analyst", "jd": "scan/jd/acme.txt", "resume": "resumes/tier2.md"},
 {"id": "globex-detection-eng", "jd": "scan/jd/globex.txt", "resume": "resumes/tier1.md"}]
```

**`spec.md`** - the only domain file. Two sections, `## Worker` and
`## Verifier`. Any `{key}` is replaced with that unit's value for `key`. See
`examples/spec.md`.

The verifier section is the part worth spending time on. Write it to hunt for a
defect, not to confirm a success. A verifier prompted to check whether the work
looks good returns yes every time.

## Procedure

1. **Resolve paths.** Checkpoint dir defaults to `<units.json dir>/checkpoint`.
   Create it. Say the absolute path out loud so the user can inspect it.

2. **Compute the todo list.** Always, resume or not. A fresh run is just a run
   where nothing is verified yet, so there is no separate code path:

   ```
   python ${CLAUDE_PLUGIN_ROOT}/skills/batch-run/scripts/state.py todo --units <units.json> --dir <checkpoint>
   ```

   Without `--resume` the user may want a clean run: ask before deleting
   checkpoint files, then delete and recompute. Never silently reuse state the
   user did not ask you to reuse, and never silently discard it either.

3. **Read `spec.md`** and split it on the `## Worker` and `## Verifier`
   headings. Workflow scripts cannot read files, so the text has to be passed in.

4. **Project the cost, and ask before spending real money.**

   ```
   python ...\scripts\estimate.py project --units <units.json> --spec <spec.md> --text
   ```

   First check `load-config`: if a prior run already chose models, reuse them
   silently. A resume must not re-decide something the user already answered.

   ```
   python ...\scripts\estimate.py load-config --dir <checkpoint>
   ```

   If `ask` is true (default: over 100k projected output tokens, which is 53
   units at the default per-unit assumption), stop and put the menu to the user
   with `AskUserQuestion` before launching anything. Show the projection, and
   say out loud that it is rough. Then record the answer so the resume inherits
   it:

   ```
   python ...\scripts\estimate.py save-config --dir <checkpoint> --units <units.json> --spec <spec.md> --choice <key>
   ```

   **If you cannot ask** (background run, non-interactive session), do not
   stall and do not silently downgrade. Run at Sonnet on both stages, and say
   in your final message that the gate was skipped and why.

   Two things the menu encodes that are easy to get wrong:

   - **A model downgrade cuts price per token, not token count.** Price the
     options in dollars. A lower `effort` is the opposite: same rate, fewer
     tokens, by an amount this does not measure. Do not claim a percentage.
   - **Downgrade the writer before the verifier.** They fail differently. A
     cheap writer produces a weak artifact that the verifier still catches. A
     cheap verifier waves through an invented certification, and nobody looks
     at that unit again.

5. **Launch the workflow.** This skill instructs you to call `Workflow`, which
   satisfies the opt-in requirement. Use `scriptPath`, not an inline script:

   ```
   Workflow({
     scriptPath: "${CLAUDE_PLUGIN_ROOT}/skills/batch-run/workflows/batch.js",
     args: {
       units: [...],                 // the todo list from step 2, verbatim
       worker: "...",                // the ## Worker section
       verifier: "...",              // the ## Verifier section
       checkpointDir: "<abs path>",
       statePath: "${CLAUDE_PLUGIN_ROOT}/skills/batch-run/scripts/state.py",
       maxAttempts: 2,
       models: { worker: {...}, verifier: {...} }   // from step 4's config
     }
   })
   ```

   It runs in the background and notifies you when it finishes. Do not poll it.

6. **Report from disk, not from the workflow's return value:**

   ```
   python ${CLAUDE_PLUGIN_ROOT}/skills/batch-run/scripts/state.py report --units <units.json> --dir <checkpoint>
   ```

7. **If anything is not verified, render the handoff:**

   ```
   python ...\scripts\handoff.py --units <units.json> --dir <checkpoint> --spec <spec.md> --slug <slug>
   ```

   Run these through PowerShell. Git Bash rewrites a leading slash in an
   argument into a Windows path, so a literal `/batch-run ...` handed to a
   script through Bash arrives as `C:/Program Files/Git/batch-run ...`. The
   resume command is generated inside `handoff.py` to dodge exactly that, but
   the same trap catches any other slash-prefixed argument you pass by hand.

8. **Tell the user** the counts, each failure with its reason, the models used,
   and the one command that resumes. Keep it to a few lines; the handoff doc
   holds the detail.

## Scale

Concurrency is capped at `min(16, cores - 2)` per run, so a batch of 200 units
is fine to submit whole. Sonnet is the floor on both stages: the workflow sets
`opts.model` explicitly rather than inheriting the session model, because
inheriting would run a 30-unit batch at 60 Opus agents without saying so.

Haiku 4.5 rejects the `effort` parameter outright. `estimate.py` refuses to
build an option that pairs the two, and `batch.js` drops an effort override on
a Haiku stage rather than passing it through to fail at launch.

## Failure modes worth knowing

- **`resumeFromRunId` is same-session only.** It rescues an edited script or a
  killed run inside one session. It does not survive `/clear` or a dead session.
  The on-disk checkpoint is what covers that, which is why both exist.
- **A unit that fails verification twice needs a prompt fix, not another retry.**
  Two independent rejections usually means the spec is asking for something the
  source material cannot support.
- **Concurrent runs of the same batch** are not prevented by default. If you
  genuinely need that, wrap each unit in `state.py claim` / `release`. A single
  workflow run dispatches each unit once and does not need it.
- **Deleting a record file is the supported way to force a redo** of one unit.

## Layout

- `scripts/state.py` - the checkpoint store and its CLI. The whole invariant lives here.
- `scripts/handoff.py` - renders a handoff doc from the checkpoint dir, on the way back up.
- `workflows/batch.js` - the fan-out: write, then refute, with one retry.
- `tests/test_state.py` - 27 tests, mostly simulated interruptions. Run with
  `python -m pytest tests/ -q` from the skill dir.
- `examples/` - a runnable units and spec pair.
