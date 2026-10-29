#!/usr/bin/env python3
"""Fail when a generated shared skill differs from what claude-config wrote.

claude-config's bin/sync-shared-skills.py writes the shared skills into this
repo and records each file's sha256 in .shared-skills.json at the repo root.
This script, a copy that the sync also writes, checks the files against that
record, and checks the executable bit of each file against the record's
"executable" list. Windows has no x bit, so the mode check runs only on
Linux and the Mac. Run it from the repo root. Exit 0 when every generated folder matches,
exit 1 after listing each changed, missing or added file.

To change a generated skill, edit it in claude-config/skills/, then rerun
bin/sync-shared-skills.py there. A hand edit here fails this check.
Standard library only.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

LOCK = ".shared-skills.json"
IGNORED_DIRS = {"__pycache__", ".pytest_cache"}
IGNORED_SUFFIXES = (".pyc", ".pyo")


def ignored(rel_parts):
    return any(p in IGNORED_DIRS for p in rel_parts) or rel_parts[-1].endswith(IGNORED_SUFFIXES)


def problems(repo):
    repo = Path(repo)
    try:
        lock = json.loads((repo / LOCK).read_text(encoding="utf-8"))
        files = lock["files"]
        dirs = lock["dirs"]
        executable = set(lock.get("executable", []))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ["{} cannot be read: {}".format(LOCK, exc)]
    found = []
    for rel, digest in sorted(files.items()):
        path = repo / rel
        if not path.is_file():
            found.append("missing: {}".format(rel))
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            found.append("changed: {}".format(rel))
        elif os.name != "nt" and bool(path.stat().st_mode & 0o111) != (rel in executable):
            found.append("mode: {} should be {}".format(rel, "755" if rel in executable else "644"))
    for d in sorted(dirs):
        base = repo / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(repo).parts
            rel = "/".join(rel_parts)
            if not ignored(rel_parts) and rel not in files:
                found.append("added: {}".format(rel))
    return found


def main():
    found = problems(Path.cwd())
    if not found:
        print("shared skills match {}".format(LOCK))
        return 0
    for line in found:
        print(line)
    print("{} file(s) differ from the form claude-config generated. Edit the skill in "
          "claude-config/skills/ and rerun bin/sync-shared-skills.py there.".format(len(found)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
