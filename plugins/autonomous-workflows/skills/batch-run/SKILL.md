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
   The one exception: if the result carries a `remapped` list, `load-config`
   corrected the saved record. A stage that named a model no longer allowed (an
   old Sonnet checkpoint, or a near miss such as `claude-opusx`) moved to that
   stage's default, Haiku writer or Opus verifier. A spelling such as `Opus 5.5`
   or `us.anthropic.claude-opus-5-5` became its alias. An effort that is not
   `low`, `medium`, `high`, `xhigh` or `max`, and any effort on Haiku, was
   dropped. A key other than `model` or `effort` inside a stage was dropped.
   `choice` now names the menu option the stages match, or `custom`.
   The corrected record is written back to the checkpoint, so the next resume
   loads it clean; if that write failed, the last entry says `not written back`,
   and that entry can be the only one.
   Run on the models `load-config` returned and tell the user each entry in your
   final message.

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
   stall and do not silently downgrade. Run the default (Haiku writer, Opus
   verifier), and say in your final message that the gate was skipped and why.

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
is fine to submit whole. The default is a Haiku writer and an Opus verifier.
The workflow sets `opts.model` on every agent rather than inheriting the session
model. For 30 units, an Opus session runs 30 Haiku writers and 30 Opus
verifiers on the first attempt, plus one writer and one verifier for every
retry, rather than 60 or more Opus agents. A writer that dies gets no
verifier, so its attempt adds a writer only.

Claude Code can override that model, and the script cannot see it happen. In
Claude Code 2.1.296:

- If `CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL` holds anything other than an empty
  string or `inherit`, or `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` is on, the
  workflow runtime ignores the model `agent()` passes and logs
  `Workflow agent model "..." ignored`. The runtime reads the variable with
  `D.str`, which trims it, so a value made only of spaces counts as unset.
- `ANTHROPIC_DEFAULT_OPUS_MODEL` and `ANTHROPIC_DEFAULT_HAIKU_MODEL` point the
  aliases at whatever model they name.
- An `availableModels` allowlist that leaves out the model swaps in the newest
  allowed model in its family, or the parent model.
- Reading the binary's model resolver, a stage set to `opus` in a session that
  already runs an Opus model appears to get the session's Opus model. That is
  unverified; no live run confirmed it.

If any of these apply, the stages may run on another model, Sonnet included,
and the figures below are for the wrong model.

The menu prices the models the aliases resolve to on the Claude API: Opus 5.5
at $4 / $20 per million input / output tokens, and Haiku 5.5 at $0.10 / $0.50,
or $0.50 / $2.50 for a request whose prompt is over 100K tokens. On other
providers Claude Code serves older models under the same aliases. In the alias
table of Claude Code 2.1.296:

- `haiku` is Haiku 4.5 ($1 / $5) on Amazon Bedrock, Google Vertex AI and
  Microsoft Foundry, and on the providers that table names only by the ids
  `mantle`, `anthropic_aws`, `anthropic_google_cloud` and `gateway`. On those
  providers the Haiku share of each figure is about ten times too low for a
  prompt of 100K tokens or fewer, and about half the real price above that,
  where Haiku 4.5 still charges $1 / $5.
- `opus` is Opus 4.6 on Foundry and Opus 4.7 on `gateway`. The binary's own
  price table lists both at $5 / $25, so on those two the Opus share of each
  figure is 80% of the real price. That table was not checked against
  Anthropic's price page. The binary's model catalog lists no `xhigh` effort
  for Opus 4.6. The menu only ever sets `low`, but `load-config` and
  `batch.js` keep a hand-set `xhigh` or `max` on Opus and pass it through
  after trimming and lowercasing.

The same binary also carries a second alias table. It maps `haiku` to Haiku 4.5
on every provider, and `opus` to Opus 5 everywhere except Foundry (Opus 4.6)
and `gateway` (Opus 4.7). Its price table lists Opus 5 at $5 / $25. Under that
table the Haiku share is low on every provider and the Opus share is 80% of the
real price. Which table a given session uses is unverified.

The binary's fast-mode price lookup returns $8 / $40 for Opus 5.5, twice the
menu's figure. Whether workflow agents run in fast mode is unverified.

Haiku 5.5 takes an `effort` level and Haiku 4.5 does not. Nothing in this skill
can tell which of the two the alias will reach, so `estimate.py` refuses to
build an option that pairs Haiku with an effort override, `load-config` drops
one, and `batch.js` drops one on a Haiku stage and logs it rather than passing
it through.

## Failure modes worth knowing

- **`resumeFromRunId` is same-session only.** It rescues an edited script or a
  killed run inside one session. It does not survive `/clear` or a dead session.
  The on-disk checkpoint is what covers that, which is why both exist.
- **A unit that fails verification twice needs a prompt fix, not another retry.**
  Two independent rejections usually means the spec is asking for something the
  source material cannot support.
- **Concurrent runs of the same batch** are not prevented by default. If you
  genuinely need that, wrap each unit in `state.py claim` / `release`. A single
  workflow run dispatches each unit once and does not need it. `claim` exits 0
  and prints `claimed` when the caller wins, exits 1 and prints
  `held-by-another` when it loses, exits 2 on a usage error such as a negative
  `--lease-seconds`, and exits 3 when it cannot create the lock file. Any other
  error prints a Python traceback and also exits 1, so check for
  `held-by-another` on stdout rather than the exit code alone. A stale lock
  the caller cannot delete reads as held on every call.
- **Deleting a record file is the supported way to force a redo** of one unit.

## Layout

- `scripts/state.py` - the checkpoint store and its CLI. The whole invariant lives here.
- `scripts/handoff.py` - renders a handoff doc from the checkpoint dir, on the way back up.
- `workflows/batch.js` - the fan-out: write, then refute, with one retry.
- `scripts/estimate.py` - the cost projection, the model menu and the saved choice.
- `tests/test_state.py` - the checkpoint store, mostly simulated interruptions.
- `tests/test_estimate.py` - the projection, the menu and the saved choice.
- `tests/test_batch_js.py` - the model and effort rules in `batch.js`, run under
  node; skipped when node is not on PATH.
- Run the tests with `python -m pytest tests/ -q` from the skill dir.
- `examples/` - a runnable units and spec pair.
