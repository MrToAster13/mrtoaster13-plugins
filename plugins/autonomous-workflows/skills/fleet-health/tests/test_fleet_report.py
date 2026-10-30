"""Tests for the fleet-health report-only mode (ELI-866).

Every repo here is a temp git repo built by the test. Nothing reads the real
home directory. One test per acceptance criterion in ELI-866, plus the
boundary cases each criterion implies.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import fleet_report  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch, tmp_path):
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[init]\n\tdefaultBranch = master\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)


def git(cwd, *args):
    out = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True,
                         text=True, check=True)
    return out.stdout.strip()


def commit(repo, name, text="x"):
    (Path(repo) / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "add " + name)


def bare_remote(tmp_path, name="remote.git", branch="master"):
    """A bare repo with one commit on branch, and HEAD pointing at it."""
    seed = tmp_path / ("seed-" + name)
    seed.mkdir()
    git(seed, "init", "-q", "-b", branch)
    commit(seed, "README.md", "seed")
    bare = tmp_path / name
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    return bare


def clone(src, dest):
    subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True)
    return Path(dest)


def tree_digest(root):
    """sha256 over every file path, size and content under root, .git included."""
    h = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            h.update(str(path.relative_to(root)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def by_path(reports):
    return {os.path.normcase(str(Path(r["path"]).resolve())): r for r in reports}


def key(p):
    return os.path.normcase(str(Path(p).resolve()))


# AC1: a report-only mode, named in SKILL.md, that runs no fetch and changes
# no ref, file or branch.

def test_skill_md_names_the_report_only_mode_and_its_script():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "Report-only mode" in text
    assert "scripts/fleet_report.py" in text


def test_report_mode_runs_no_fetch_and_changes_nothing(tmp_path):
    remote = bare_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    repo = clone(remote, home / "app")
    commit(repo, "local.txt")
    (repo / "dirty.txt").write_text("dirty")
    git(repo, "branch", "feature")
    git(repo, "worktree", "add", "-q", str(home / "app-wt"), "feature")
    (home / "old-backup-2026").mkdir()
    # The remote moves on. A fetch would advance origin/master in the clone.
    other = clone(remote, tmp_path / "other")
    commit(other, "upstream.txt")
    git(other, "push", "-q", "origin", "master")
    before_ref = git(repo, "rev-parse", "refs/remotes/origin/master")
    before = tree_digest(home)

    rc = fleet_report.main(["--root", str(home), "--home", str(home), "--json"])

    assert rc == 0
    assert git(repo, "rev-parse", "refs/remotes/origin/master") == before_ref
    assert tree_digest(home) == before


# AC2: discovery finds linked worktrees (.git file) as well as clones.

def test_discovery_finds_clones_and_linked_worktrees_and_skips_build_dirs(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    main = home / "proj"
    main.mkdir()
    git(main, "init", "-q")
    commit(main, "a.txt")
    git(main, "branch", "side")
    wt = home / "nested" / "proj-wt"
    git(main, "worktree", "add", "-q", str(wt), "side")
    junk = home / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    git(junk, "init", "-q")

    found = {key(p) for p in fleet_report.discover([home])}

    assert key(main) in found
    assert key(wt) in found
    assert (wt / ".git").is_file()
    assert key(junk) not in found


# AC3: per repo it reports origin URL, branch, dirty count, commits on no
# remote, stash count and worktree list entries.

def test_repo_report_fields(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "app")
    git(repo, "switch", "-q", "-c", "eli-214-local")
    commit(repo, "one.txt")
    commit(repo, "two.txt")
    (repo / "README.md").write_text("changed")
    (repo / "new.txt").write_text("untracked")
    (repo / "stashme.txt").write_text("s")
    git(repo, "add", "stashme.txt")
    git(repo, "stash", "push", "-q", "--", "stashme.txt")
    git(repo, "worktree", "add", "-q", str(tmp_path / "home" / "app-wt"), "-b", "wt-branch")

    r = fleet_report.repo_report(repo)

    assert r["origin"] == str(remote)
    assert r["branch"] == "eli-214-local"
    assert r["dirty"] == 2
    assert r["no_remote"] == 2
    assert r["stashes"] == 1
    wts = {key(p) for p in r["worktrees"]}
    assert wts == {key(repo), key(tmp_path / "home" / "app-wt")}


def test_repo_report_on_a_repo_with_no_commits_and_no_origin(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-q")

    r = fleet_report.repo_report(repo)

    assert r["origin"] == ""
    assert r["no_remote"] == 0
    assert r["stashes"] == 0
    assert r["dirty"] == 0


# AC4: stale merged compares against each repo's own default, read from
# refs/remotes/origin/HEAD, not origin/main.

def test_stale_merged_uses_the_repos_own_default_branch(tmp_path):
    remote = bare_remote(tmp_path, branch="master")
    repo = clone(remote, tmp_path / "home" / "mc")
    git(repo, "branch", "done-feature")
    git(repo, "switch", "-q", "-c", "open-feature")
    commit(repo, "wip.txt")
    git(repo, "switch", "-q", "master")

    r = fleet_report.repo_report(repo)

    assert r["default"] == "origin/master"
    assert r["stale_merged"] == ["done-feature"]


def test_stale_merged_follows_origin_head_when_main_and_master_both_exist(tmp_path):
    remote = bare_remote(tmp_path, branch="trunk")
    seed = clone(remote, tmp_path / "seed2")
    git(seed, "switch", "-q", "-c", "main")
    commit(seed, "main-only.txt")
    git(seed, "push", "-q", "origin", "main")
    repo = clone(remote, tmp_path / "home" / "r")
    git(repo, "branch", "merged-into-trunk")

    r = fleet_report.repo_report(repo)

    assert r["default"] == "origin/trunk"
    assert "merged-into-trunk" in r["stale_merged"]


# AC5: groups dirs that share one normalized origin URL, and lists
# *-backup-* and *-test-run* folders under home that have no .git.

@pytest.mark.parametrize("url", [
    "https://github.com/MrToAster13/ebay-sheet.git",
    "https://github.com/mrtoaster13/ebay-sheet",
    "git@github.com:MrToAster13/ebay-sheet.git",
    "ssh://git@github.com/MrToAster13/ebay-sheet.git",
    "https://github.com/MrToAster13/ebay-sheet/",
])
def test_origin_urls_normalize_to_one_key(url):
    assert fleet_report.normalize_origin(url) == "github.com/mrtoaster13/ebay-sheet"


def test_duplicate_groups_share_one_origin_and_singletons_are_left_out():
    reports = [
        {"path": "/h/ebay-sheet", "origin": "https://github.com/MrToAster13/ebay-sheet.git"},
        {"path": "/h/ebay-sheet-arch", "origin": "git@github.com:MrToAster13/ebay-sheet.git"},
        {"path": "/h/solo", "origin": "https://github.com/MrToAster13/solo.git"},
        {"path": "/h/none-a", "origin": ""},
        {"path": "/h/none-b", "origin": ""},
    ]

    groups = fleet_report.group_duplicates(reports)

    assert groups == {"github.com/mrtoaster13/ebay-sheet": ["/h/ebay-sheet", "/h/ebay-sheet-arch"]}


def test_leftover_folders_lists_non_git_backups_and_test_runs_only(tmp_path):
    home = tmp_path / "home"
    for name in ["ebay-sheet-backup-2026-09-14-125656", "grc-test-run1", "grc-test-run2",
                 "plain-folder", "kept-backup-2026"]:
        (home / name).mkdir(parents=True)
    git(home / "kept-backup-2026", "init", "-q")
    (home / "notes-backup-2026.txt").write_text("a file, not a folder")

    found = fleet_report.leftover_folders(home)

    assert [Path(p).name for p in found] == [
        "ebay-sheet-backup-2026-09-14-125656", "grc-test-run1", "grc-test-run2"]


def test_json_report_carries_repos_groups_and_leftovers(tmp_path, capsys):
    remote = bare_remote(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    clone(remote, home / "a")
    clone(remote, home / "b")
    (home / "x-backup-1").mkdir()

    rc = fleet_report.main(["--root", str(home), "--home", str(home), "--json"])
    data = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert {key(r["path"]) for r in data["repos"]} == {key(home / "a"), key(home / "b")}
    assert len(data["duplicates"]) == 1
    assert [Path(p).name for p in data["leftovers"]] == ["x-backup-1"]


# Regression tests for the adversarial review findings.

def test_review1_a_repo_git_cannot_read_is_flagged_not_reported_clean(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q")
    commit(main, "a.txt")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", str(wt), "-b", "feat")
    import shutil
    shutil.rmtree(str(main / ".git" / "worktrees"))

    r = fleet_report.repo_report(wt)

    assert r.get("error")


def test_review2_report_does_not_start_the_fsmonitor_daemon(tmp_path):
    repo = tmp_path / "fsm"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "a.txt")
    git(repo, "config", "core.fsmonitor", "true")
    try:
        fleet_report.repo_report(repo)
        status = subprocess.run(["git", "-C", str(repo), "fsmonitor--daemon", "status"],
                                capture_output=True, text=True)
        assert status.returncode != 0, status.stdout
    finally:
        subprocess.run(["git", "-C", str(repo), "fsmonitor--daemon", "stop"], capture_output=True)


def test_review3_table_output_survives_a_non_utf8_stdout(tmp_path):
    home = tmp_path / "home"
    repo = home / "\u65e5\u672crepo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    out = subprocess.run([sys.executable, str(SKILL / "scripts" / "fleet_report.py"),
                          "--root", str(home), "--home", str(home)],
                         capture_output=True, env=env)

    assert out.returncode == 0, out.stderr
    assert "\u65e5\u672crepo" in out.stdout.decode("utf-8")


def test_review4_branch_checked_out_in_another_worktree_is_not_stale(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "c")
    git(repo, "worktree", "add", "-q", str(tmp_path / "home" / "wt"), "-b", "hotfix")
    git(repo, "branch", "really-stale")

    r = fleet_report.repo_report(repo)

    assert r["stale_merged"] == ["really-stale"]


def test_review5_a_repo_whose_own_folder_is_named_like_a_skip_dir_is_found(tmp_path):
    home = tmp_path / "home"
    repo = home / "code" / "tmp"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")

    found = {key(p) for p in fleet_report.discover([home])}

    assert key(repo) in found


def test_review6_a_repo_six_levels_down_is_found_by_default(tmp_path):
    home = tmp_path / "home"
    repo = home / "a" / "b" / "c" / "d" / "e" / "deep"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")

    found = {key(p) for p in fleet_report.discover([home])}

    assert key(repo) in found


def test_review7_ports_and_local_origins_normalize_correctly(tmp_path):
    n = fleet_report.normalize_origin
    assert n("ssh://git@h:2222/o/r") != n("ssh://git@h:2223/o/r")
    assert n("file:///C:/src/r") == n("C:/src/r")
    a = {"path": str(tmp_path / "x" / "one"), "origin": "../shared"}
    b = {"path": str(tmp_path / "y" / "two"), "origin": "../shared"}
    assert fleet_report.group_duplicates([a, b]) == {}


def test_security_credentials_in_the_origin_url_are_not_reported(tmp_path):
    repo = tmp_path / "cred"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://someone:s3cretvalue@github.com/o/r.git")

    r = fleet_report.repo_report(repo)

    assert "s3cretvalue" not in json.dumps(r)
    assert r["origin"] == "https://github.com/o/r.git"
