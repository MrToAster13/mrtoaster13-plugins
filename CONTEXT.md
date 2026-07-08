# clear-not-compact

A pair of Claude Code skills for carrying work across a context reset by writing state to disk and reloading it, instead of compacting a live session.

## Language

**Handoff**:
The distilled record of a session's state — goal, progress, next steps, decisions, and key files — written to disk so a fresh session can continue without the original context. Refers to both the artifact and the act of writing it.
_Avoid_: summary, dump, snapshot

**Handoff log**:
The numbered sequence of handoffs for a project. Read in order, it traces how the work evolved across sessions.
_Avoid_: history, journal

**Resume**:
Reloading the latest handoff into a fresh session, re-reading the files it names, restating the plan, and waiting for the user before continuing.
_Avoid_: restore, rehydrate

**Distill**:
Extracting the decisions and state that matter, as opposed to copying the conversation verbatim. A handoff is distilled, never transcribed.
_Avoid_: summarize, transcribe

**Clear**:
Emptying the context to a fresh, empty session. The reset this workflow is built around.
_Avoid_: reset, wipe

**Compaction**:
Claude Code's in-session replacement of conversation history with a lossy summary. The thing this workflow exists to avoid — the session keeps going, but on a paraphrase of a paraphrase.
_Avoid_: compact (as a noun)
