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
index, and with GIT_NO_LAZY_FETCH=1 so a partial clone does not fetch missing
objects. Status blanks every filter driver and stays out of submodules, so
no clean filter runs. git still
refreshes a split index's sharedindex mtime on read. Standard library only. Exit 0 on success, 2 on bad arguments or a
--root or --home that is not a directory.
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

DEFAULT_PORTS = {"ssh": "22", "git+ssh": "22", "ssh+git": "22", "https": "443",
                 "http": "80", "git": "9418"}
SKIP_DIRS = {"appdata", "temp", "tmp", ".cache", "node_modules", ".venv", "venv",
             "__pycache__", "$recycle.bin"}
LEFTOVER_PATTERNS = ("*-backup-*", "*-test-run*")
MAX_DEPTH = 6
# `git rev-parse --local-env-vars`: an inherited GIT_DIR or GIT_INDEX_FILE, as in
# a hook, would point every call at one repo.
GIT_LOCAL_ENV = ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
                 "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
                 "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
                 "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
                 "GIT_SHALLOW_FILE", "GIT_COMMON_DIR")


def _git(path, *args, config=()):
    """Run a read-only git command in path. Return stdout, or None on failure.

    core.fsmonitor=false stops `git status` from starting a watcher daemon, and
    log.showSignature=false stops `git stash list` from running gpg.program.
    config is (key, value) pairs, passed through GIT_CONFIG_KEY_n because a
    key may hold an = that `-c` would split on.
    """
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCAL_ENV}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")
    env["GIT_CONFIG_COUNT"] = str(len(config))
    for i, (k, v) in enumerate(config):
        env["GIT_CONFIG_KEY_%d" % i] = k
        env["GIT_CONFIG_VALUE_%d" % i] = v
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                              "-c", "log.showSignature=false", "-C", str(path)] + list(args),
                             capture_output=True, text=True, encoding="utf-8",
                             errors="surrogateescape", timeout=60, env=env)
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
    """(default, problem): origin/HEAD's target if that ref exists, else
    origin/main or origin/master. problem is set when origin/HEAD points to a
    missing ref and neither fallback exists."""
    ref = (_git(path, "symbolic-ref", "-q", "refs/remotes/origin/HEAD") or "").strip()
    ref = ref[len("refs/remotes/"):] if ref.startswith("refs/remotes/") else ref
    for name in ([ref] if ref else []) + ["origin/main", "origin/master"]:
        if _git(path, "rev-parse", "--verify", "-q", "refs/remotes/" + name) is not None:
            return name, None
    return None, ("origin/HEAD points to missing %s" % ref) if ref else None


def _expected_git_dir(path):
    """The git dir that path's own .git names: the dir itself, or a gitdir: file's target."""
    dot = path / ".git"
    if dot.is_dir():
        return dot
    if not dot.is_file():
        return None
    try:
        with open(dot, encoding="utf-8", errors="surrogateescape") as f:
            line = f.readline(4096).rstrip("\r\n")
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    return path / line[len("gitdir:"):].strip()


def _redact(url):
    """Drop the user:password@ part of a URL. A raw @ may sit inside the
    password, and a raw / there shows as a port that is not a number."""
    m = re.match(r"^((?:[a-z][a-z0-9+.-]*::)?[a-z][a-z0-9+.-]*://)([^/]*)(.*)$", url, re.I)
    if not m:
        return url
    scheme, authority, rest = m.groups()
    if "@" in authority:
        return scheme + authority.rsplit("@", 1)[1] + rest
    if not authority.startswith("[") and re.match(r"^[^:]*:(?!\d*$)", authority) and "@" in rest:
        return scheme + rest.rsplit("@", 1)[1]
    return url


def _no_filters(path):
    """Config that blanks every configured filter driver, so `git status`
    re-hashing a touched file runs no clean filter (git-lfs, for one)."""
    names = _lines(_git(path, "config", "--name-only", "--get-regexp", r"^filter\..*\.(clean|process)$"))
    out = []
    for driver in sorted({n[len("filter."):].rsplit(".", 1)[0] for n in names}):
        out += [("filter.%s.clean" % driver, ""), ("filter.%s.process" % driver, ""),
                ("filter.%s.required" % driver, "false")]
    return out


def _same_dir(a, b):
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def repo_report(path):
    """One repo's state, read without changing anything."""
    path = Path(path)
    failed = []

    def required(*args, config=()):
        out = _git(path, *args, config=config)
        if out is None:
            failed.append(args[0])
            return ""
        return out

    git_dir = _git(path, "rev-parse", "--absolute-git-dir")
    git_dir = git_dir.rstrip("\n") if git_dir is not None else None
    expected = _expected_git_dir(path)
    if git_dir is None:
        failed.append("rev-parse")
    elif expected is None or not _same_dir(git_dir, expected):
        # An empty or broken .git lets git walk up and read the parent repo.
        return {"path": str(path), "origin": "", "branch": "", "dirty": 0, "no_remote": 0,
                "stashes": 0, "worktrees": [], "default": None, "stale_merged": [],
                "error": "git reads this folder through %s; check it by hand" % git_dir}
    bare = ((path / ".git").is_file()
            and (_git(path, "rev-parse", "--is-bare-repository") or "").strip() == "true")
    origin = _redact((_git(path, "config", "--get", "remote.origin.url") or "").strip())
    head_ref = (_git(path, "symbolic-ref", "-q", "HEAD") or "").strip()
    branch = head_ref[len("refs/heads/"):] if head_ref.startswith("refs/heads/") else "(detached)"
    # A bare repo, such as the .bare behind a worktrees-only layout, has no files
    # to be dirty, and its linked worktrees' rows show the shared stash.
    # --ignore-submodules=dirty keeps status out of each submodule, whose own
    # filters it would run; a submodule's changes show in its own row.
    dirty = 0 if bare else len(_lines(required("status", "--porcelain=v1", "--untracked-files=normal",
                                               "--ignore-submodules=dirty",
                                               config=_no_filters(path))))
    stashes = 0 if bare else len(_lines(required("stash", "list")))
    wt_lines = _lines(required("worktree", "list", "--porcelain"))
    worktrees = [l[len("worktree "):] for l in wt_lines if l.startswith("worktree ")]
    checked_out = {l[len("branch refs/heads/"):] for l in wt_lines
                   if l.startswith("branch refs/heads/")}
    # Linked worktrees share one set of branches. The main worktree counts every
    # branch, so a pruned or unscanned worktree's commits still show; a linked
    # worktree counts its own HEAD, unless its main repo is bare and has no row.
    tips = ["HEAD"] if _git(path, "rev-parse", "--verify", "-q", "HEAD") is not None else []
    common = (_git(path, "rev-parse", "--path-format=absolute", "--git-common-dir") or "").rstrip("\n")
    shared_bare = (_git(path, "config", "--bool", "core.bare") or "").strip() == "true"
    if bare or shared_bare or (git_dir and common and _same_dir(git_dir, common)):
        tips.append("--branches")
    no_remote = int(required("rev-list", "--count", *tips, "--not", "--remotes").strip() or 0) if tips else 0
    default, problem = _default_branch(path)
    if problem:
        failed.append("symbolic-ref (%s)" % problem)
    stale = []
    if default:
        base = default.split("/", 1)[1] if "/" in default else default
        merged = _lines(required("for-each-ref", "--merged", "refs/remotes/" + default,
                                 "--format=%(refname:lstrip=2)", "refs/heads"))
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
        "error": ("git %s failed; check this repo by hand" % ", ".join(failed)) if failed else None,
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
    m = re.match(r"^([a-z][a-z0-9+.-]*)://(?:[^/]*@)?([^/:]+)(?::(\d+))?/(.*)$", u, re.I)
    if m:
        scheme, host, port, rest = m.group(1).lower(), m.group(2), m.group(3), m.group(4)
        if port and DEFAULT_PORTS.get(scheme) != port:
            host += ":" + port
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


def _submodules(path):
    """Checked-out submodule dirs of path, from the gitlinks in its index.
    A gitlink that resolves outside path, through a symlink or .., is skipped."""
    out = _git(path, "ls-files", "--stage", "-z") or ""
    top = Path(path).resolve()
    subs = [Path(path) / e.split("\t", 1)[1] for e in out.split("\0") if e.startswith("160000 ")]
    return [d for d in subs if top in d.resolve().parents
            and os.path.lexists(os.path.join(str(d), ".git"))]


def build_report(roots, home, max_depth=MAX_DEPTH):
    # Status stays out of submodules, so every submodule needs its own row,
    # including ones too deep or under a skipped folder for discover to find.
    queue = list(discover(roots, max_depth))
    seen = {os.path.normcase(str(Path(p).resolve())) for p in queue}
    repos = []
    while queue:
        path = queue.pop(0)
        repos.append(repo_report(path))
        for sub in _submodules(path):
            k = os.path.normcase(str(sub.resolve()))
            if k not in seen:
                seen.add(k)
                queue.append(sub)
    repos.sort(key=lambda r: r["path"].lower())
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
    ap.add_argument("--home", help="where to look for leftover folders (default: home)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--max-depth", type=int, default=MAX_DEPTH, help="how deep to look for repos")
    args = ap.parse_args(argv)
    home = args.home or str(Path.home())
    roots = args.root or [home]
    for d in roots + ([args.home] if args.home else []):
        if not os.path.isdir(d):
            ap.error("not a directory: %s" % d)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="surrogateescape")
    report = build_report(roots, home, args.max_depth)
    print(json.dumps(report, indent=2) if args.json else _text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
