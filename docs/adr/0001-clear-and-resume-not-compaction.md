# Carry context across resets with clear-and-resume, not compaction

The problem is context rot: `/compact` keeps a session alive but swaps its real history for a lossy summary, and quality drifts as the model reasons over a paraphrase of a paraphrase. We chose the opposite shape — a two-session loop. Session one distills state into a handoff doc on disk (`/handoff`); the user runs `/clear` to an empty context; session two reloads the doc (`/resume`) and continues. Every session starts from real, hand-picked state instead of an accumulating summary.

## Consequences

- The user must run `/clear` by hand. A skill can't invoke slash commands, so `/handoff` writes the doc and then tells the user to clear. One manual keystroke is the cost of the whole approach.
- This pays off most on models that degrade through compaction — pronounced on Sonnet, largely absent on recent Opus. On Opus you can lean on `/compact` and reserve this loop for long, multi-day builds.
