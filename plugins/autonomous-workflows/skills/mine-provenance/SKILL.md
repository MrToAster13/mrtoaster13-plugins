---
name: mine-provenance
description: Audit a skills/portfolio doc against ground-truth artifacts, then extend it. Verifies each claim with a git commit, file, or test result; sorts everything into VERIFIED, GAPS (claimed but unproven), and PROVENANCE-FLAG (evidence shows forked/derived). Transcripts are used only as discovery leads, never as proof. Never argues with the user's claims — it sorts them. Use when the user says "mine-provenance", "audit my portfolio", "verify my skills claims", "provenance check", or wants their portfolio backed by evidence.
---

# Mine Provenance

Make a portfolio honest by tying every claim to a ground-truth artifact — a commit, a file, a passing test — and by calling out anything the evidence says is derived rather than originated. Overclaiming is the real risk here: a portfolio that quietly presents a *forked* repo as *originated* is the failure mode this prevents.

Two rules hold the whole skill together:

- **Artifacts verify; transcripts only lead.** A claim becomes VERIFIED only when a commit, file, or test backs it. Session transcripts are conversation, not proof — use them to *find* work worth checking, never as a citation.
- **Never argue; sort.** Do not dismiss or debate a claim in conversation. Put it in the right bucket and let the evidence speak. "Unproven" and "derived" are buckets, not verdicts on the user.

## Steps

1. **Locate the portfolio doc.** Use the path the user gives, or default to a `skills.md` / portfolio doc in the current repo. Read it and any provenance definitions it references (e.g. a `CONTEXT.md` defining "Built" vs "Modified"). This doc's prose is the user's — you audit and extend it, you never overwrite it.

2. **Extract the claims.** Pull out each discrete claim: every skill, accomplishment, or "this proves X" statement, with whatever provenance label it already carries.

3. **Verify each claim against artifacts only.** For each claim, hunt for a backing artifact and record the exact reference:
   - `git log --follow`, `git blame`, and commit hashes for authorship and history.
   - The file(s) that embody the skill.
   - A test result or CI record where the claim is "it works / it passes."
   A claim is **VERIFIED** only with a concrete `path`, `commit`, or test reference. Note any **drift** — the doc says something the artifacts contradict (wrong count, renamed file, claim no longer true).

4. **Run the provenance check — is it originated or derived?** For each repo or artifact behind a claim, look for derivation signals and weigh them:
   - `git remote -v` and (if `gh` is available) the GitHub fork relationship.
   - Early commit history that looks like a bulk import/squash of someone else's tree rather than incremental authorship.
   - A `LICENSE` or headers naming another author; README credits; a "Modified"/"forked from" note.
   If the evidence says the work is forked, derived, or built on upstream, it goes to the **PROVENANCE-FLAG** bucket with the specific signal (e.g. "remote points at upstream X"; "initial commit imports N files authored by Y"). Do not soften this and do not argue it — record what the evidence shows and let the user word the final claim.

5. **Use transcripts only as leads.** To discover work that never left a clean artifact, `grep` the session transcripts for a claim's keywords and read only the matching snippets — never bulk-read the logs. A transcript can point you toward a commit or file to verify; it can never be the citation itself. If a lead has no backing artifact, it's a gap, not a verified claim.

6. **Find newly-provable skills.** Scan the repos for real, evidenced skills the doc doesn't list yet. Propose each as an addition **with its artifact citation attached** — never a bare suggestion. Draft the additions in the doc's existing voice and provenance format, but as *proposals* for the user to accept, not silent edits.

7. **Write the three outputs.**
   - **VERIFIED** → proposed additions/corrections to the portfolio doc, each with its `commit`/`path`/test citation. Presented for the user to merge; the prose stays theirs.
   - **`GAPS.md`** → claims with no backing artifact found. Framed as "needs verification," with the exact search you ran so the user can point you at the missing evidence.
   - **`PROVENANCE.md`** → every derivation flag, with the concrete signal and the artifact reference. This is the honesty layer that keeps the portfolio from overclaiming.

8. **Report.** Summarize: how many claims verified, how many drifted, how many gaps, how many provenance flags, and how many new evidenced skills you can add. Point the user at the three files and let them decide the wording.

## Notes

- Every VERIFIED line carries a real reference — commit hash, file path, or test result. No citation, no verification; it goes to GAPS instead.
- Transcripts never back a claim. They are 116MB of conversation for a reason — leads, not proof.
- A provenance flag is not an accusation. It's what the git history shows, recorded plainly, so the user can present derived work as derived and originated work as originated.
- Never rewrite the user's portfolio prose. Propose; don't overwrite.
- When evidence is absent, say "no artifact found," not "this didn't happen." Absence of proof is a gap to fill, not a claim to reject.
