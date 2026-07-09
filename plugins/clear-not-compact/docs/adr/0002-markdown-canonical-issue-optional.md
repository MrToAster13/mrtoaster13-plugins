# The markdown handoff file is canonical; the GitHub issue is an optional index

State has to survive `/clear` in any repo, including one with no GitHub remote and no `gh` auth. So `/handoff` always writes a markdown file under `docs/handoffs/` as the source of truth. When a remote and `gh` are present it can also file a GitHub issue, but only as an index that links back to the file, and only with the user's explicit yes — filing an issue is outward-facing.

## Considered options

- **GitHub issues only** (the approach in the source Reddit thread): gives a clean track record, but fails silently when there's no remote or no auth, which would defeat the entire purpose. Rejected as the sole store; kept as an optional layer on top of the file.
