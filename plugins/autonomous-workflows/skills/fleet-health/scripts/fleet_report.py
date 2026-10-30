#!/usr/bin/env python3
"""fleet-health report-only mode: read every local git repo, change nothing.

    python fleet_report.py [--root PATH ...] [--home PATH] [--json]

Finds clones and linked worktrees (a `.git` file) under each root, then
reports per repo: origin URL, branch, dirty count, commits on no remote,
stash count, `git worktree list` entries, the repo's own default branch and
the local branches already merged into it. It also groups directories that
share one normalized origin URL and lists `*-backup-*` and `*-test-run*`
folders directly under home that have no `.git`.

Read-only: no fetch, no push, no ref, branch, file or config change. Every
git call runs with --no-optional-locks so `git status` does not rewrite the
index. Standard library only. Exit 0 on success, 2 on bad arguments.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {"appdata", "temp", "tmp", ".cache", "node_modules", ".venv", "venv",
             "__pycache__", "$recycle.bin"}
LEFTOVER_PATTERNS = ("*-backup-*", "*-test-run*")
MAX_DEPTH = 6


def _git(path, *args):
    """Run a read-only git command in path. Return stdout, or None on failure.

    core.fsmonitor=false stops `git status` from starting a watcher daemon.
    """
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                              "-C", str(path)] + list(args),
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def discover(roots, max_depth=MAX_DEPTH):
    """Repo dirs under roots: any dir holding a `.git` dir or `.git` file."""
    found = []
    seen = set()
    for root in roots:
        root = Path(root)
        stack = [(root, 0)]
        while stack:
            d, depth = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            names = {e.name for e in entries}
            if ".git" in names:
                k = os.path.normcase(str(d.resolve()))
                if k not in seen:
                    seen.add(k)
                    found.append(d)
            if depth >= max_depth:
                continue
            for e in entries:
                if e.name == ".git":
                    continue
                try:
                    if not e.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if e.name.lower() in SKIP_DIRS:
                    # A repo may itself be named tmp or venv. Check it, do not descend.
                    if os.path.lexists(os.path.join(e.path, ".git")):
                        k = os.path.normcase(str(Path(e.path).resolve()))
                        if k not in seen:
                            seen.add(k)
                            found.append(Path(e.path))
                    continue
                stack.append((Path(e.path), depth + 1))
    return sorted(found, key=lambda p: str(p).lower())


def _lines(text):
    return [l for l in (text or "").splitlines() if l.strip()]


def _default_branch(path):
    ref = _git(path, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    if ref and ref.strip():
        return ref.strip()
    for name in ("origin/main", "origin/master"):
        if _git(path, "rev-parse", "--verify", "-q", "refs/remotes/" + name) is not None:
            return name
    return None


def repo_report(path):
    """One repo's state, read without changing anything."""
    path = Path(path)
    error = None
    if _git(path, "rev-parse", "--git-dir") is None:
        error = "git cannot read this repo; check it by hand"
    origin = (_git(path, "config", "--get", "remote.origin.url") or "").strip()
    # Never print a user:token@ part of a URL.
    origin = re.sub(r"^([a-z][a-z0-9+.-]*://)[^@/]*@", r"\1", origin, flags=re.I)
    branch = (_git(path, "symbolic-ref", "--short", "-q", "HEAD") or "").strip() or "(detached)"
    dirty = len(_lines(_git(path, "status", "--porcelain=v1")))
    has_head = _git(path, "rev-parse", "--verify", "-q", "HEAD") is not None
    no_remote = 0
    if has_head:
        no_remote = int((_git(path, "rev-list", "--count", "HEAD", "--not", "--remotes") or "0").strip() or 0)
    stashes = len(_lines(_git(path, "stash", "list")))
    wt_lines = _lines(_git(path, "worktree", "list", "--porcelain"))
    worktrees = [l[len("worktree "):] for l in wt_lines if l.startswith("worktree ")]
    checked_out = {l[len("branch refs/heads/"):] for l in wt_lines
                   if l.startswith("branch refs/heads/")}
    default = _default_branch(path)
    stale = []
    if default:
        base = default.split("/", 1)[1] if "/" in default else default
        merged = _lines(_git(path, "for-each-ref", "--merged", default,
                             "--format=%(refname:short)", "refs/heads"))
        stale = [b for b in merged if b not in (branch, base) and b not in checked_out]
    return {
        "path": str(path),
        "origin": origin,
        "branch": branch,
        "dirty": dirty,
        "no_remote": no_remote,
        "stashes": stashes,
        "worktrees": worktrees,
        "default": default,
        "stale_merged": stale,
        "error": error,
    }


def _local_key(u, base):
    if base is not None and not os.path.isabs(u):
        u = os.path.join(str(base), u)
    return os.path.normcase(os.path.abspath(u))


def normalize_origin(url, base=None):
    """host[:port]/owner/repo, lowercased, for any https, ssh or scp-style URL.

    A local path or file:// URL becomes an absolute path. A relative one is
    resolved against base, the repo dir it belongs to.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if u.lower().startswith("file://"):
        u = u[len("file://"):]
        if re.match(r"^/[a-zA-Z]:", u):
            u = u[1:]
        return _local_key(u, base)
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+(?::\d+)?)/(.*)$", u, re.I)
    if m:
        host, rest = m.group(1), m.group(2)
    else:
        m = re.match(r"^(?:[^@/]+@)?([^/:]+):(.*)$", u)
        if m and not re.match(r"^[a-zA-Z]$", m.group(1)):
            host, rest = m.group(1), m.group(2)
        else:
            return _local_key(u, base)
    rest = rest.strip("/")
    if rest.endswith(".git"):
        rest = rest[:-4]
    return (host + "/" + rest.strip("/")).lower()


def group_duplicates(reports):
    """{normalized origin: [paths]} for origins shared by two or more dirs."""
    groups = {}
    for r in reports:
        k = normalize_origin(r.get("origin", ""), base=r["path"])
        if k:
            groups.setdefault(k, []).append(r["path"])
    return {k: v for k, v in sorted(groups.items()) if len(v) > 1}


def leftover_folders(home):
    """Top-level folders under home that match a leftover pattern and have no .git."""
    home = Path(home)
    out = []
    try:
        entries = sorted(os.scandir(home), key=lambda e: e.name.lower())
    except OSError:
        return out
    for e in entries:
        if not e.is_dir(follow_symlinks=False):
            continue
        if not any(fnmatch.fnmatch(e.name.lower(), p) for p in LEFTOVER_PATTERNS):
            continue
        if os.path.lexists(os.path.join(e.path, ".git")):
            continue
        out.append(e.path)
    return out


def build_report(roots, home, max_depth=MAX_DEPTH):
    repos = [repo_report(p) for p in discover(roots, max_depth)]
    return {"repos": repos, "duplicates": group_duplicates(repos),
            "leftovers": leftover_folders(home)}


def _text(report):
    rows = ["path | branch | dirty | no remote | stashes | worktrees | default | stale merged | origin"]
    for r in report["repos"]:
        rows.append(" | ".join(str(x) for x in (
            r["path"], r["branch"], r["dirty"], r["no_remote"], r["stashes"],
            len(r["worktrees"]), r["default"] or "-", ",".join(r["stale_merged"]) or "-",
            r["origin"] or "-")) + ("  ERROR: " + r["error"] if r["error"] else ""))
    rows.append("")
    rows.append("Duplicate groups (same origin):")
    for k, paths in report["duplicates"].items():
        rows.append("  %s: %s" % (k, ", ".join(paths)))
    rows.append("Leftover folders with no .git:")
    for p in report["leftovers"]:
        rows.append("  " + p)
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", action="append", help="dir to scan; repeatable (default: home)")
    ap.add_argument("--home", default=str(Path.home()), help="where to look for leftover folders")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--max-depth", type=int, default=MAX_DEPTH, help="how deep to look for repos")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = build_report(args.root or [args.home], args.home, args.max_depth)
    print(json.dumps(report, indent=2) if args.json else _text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
