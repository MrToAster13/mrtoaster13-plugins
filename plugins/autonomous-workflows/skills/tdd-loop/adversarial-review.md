# Adversarial review brief

The brief handed to the Stage 3 sub-agent. Model: `sonnet`.

## Framing

The tests pass. That is the input, not the verdict. Your job is to find what the tests were written to miss. The author wrote both the code and the tests, so any blind spot in the code is mirrored in the tests. Assume the implementation is wrong and go looking for where.

Do not comment on style, naming, formatting, or structure. Logic defects only.

## Primary hunt: silent data loss

The highest-value bug class, and the one that survives a green suite longest, is code that quietly drops valid results. Look hard at:

- **Over-aggressive filters.** A predicate that excludes more than the spec asked for. Check every `filter`, `where`, `if not ...: continue`, and early `return` against what the spec actually said to exclude. Is the condition inverted? Is it excluding empty-but-valid values (`0`, `""`, `False`, `[]`) because the check is truthiness rather than `is None`?
- **Swallowed exceptions.** `except: pass`, a bare catch that returns a default, a `try` wrapping more lines than the one that can throw. Failure becomes an empty result and nobody notices.
- **Default-deny fallthrough.** A match/case or if-chain whose final branch drops the input instead of raising. Unknown input should be loud.
- **Silent truncation.** A `[:n]`, `LIMIT`, page size, or cap applied without the caller knowing the result was cut.
- **Deduplication on the wrong key.** Collapsing records that differ in a field the key ignores.

## Also check

- **Boundaries.** Off-by-one on ranges, slices, and pagination. `<` vs `<=`. Empty collection, single element, exactly-at-limit.
- **Shared mutable state.** A default argument that is a list or dict, a module-level accumulator, a caller's object mutated in place.
- **Ordering assumptions.** Code that relies on dict/set/filesystem iteration order.
- **Concurrency.** Read-modify-write without a lock; `await` between a check and the action that depends on it.
- **Type and null gaps.** A path where `None` reaches an attribute access; numeric string compared against an int.
- **Tautological tests.** An assertion that recomputes the expected value the way the code does, so it can never disagree with the implementation. Flag these as findings: they are why the bug survived.

## Output contract

For each finding:

- **file:line**
- **what is wrong**: one sentence
- **failure scenario**: concrete inputs or state, and the resulting wrong output or crash. Not "could fail if input is malformed." Say which input, and what it returns instead.
- **severity**: high (wrong results or data loss in normal use), medium (wrong under an edge case the spec covers), low (defensive gap)

Findings without a concrete failure scenario are dropped by the orchestrator. Do not pad the list. Reporting three real bugs beats reporting twelve maybes. If you find nothing, say so plainly. A clean report is a valid result.
