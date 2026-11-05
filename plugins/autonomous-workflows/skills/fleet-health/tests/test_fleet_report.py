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


# Regression tests for the ELI-1043 verifier findings on PR #17.

def test_eli1043_f1_partial_clone_is_not_fetched_and_shows_an_error(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q")
    commit(seed, "a.txt", "".join("%d\n" % i for i in range(50)))
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(remote)], check=True)
    git(remote, "config", "uploadpack.allowfilter", "true")
    home = tmp_path / "home"
    home.mkdir()
    repo = home / "pc"
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "1")
    subprocess.run(["git", "clone", "-q", "--no-checkout", "--filter=blob:none",
                    remote.as_uri(), str(repo)], check=True)
    git(repo, "read-tree", "HEAD")
    (repo / "b.txt").write_text("".join("%d\n" % i for i in range(51)))
    git(repo, "add", "b.txt")
    git(repo, "rm", "-q", "--cached", "a.txt")
    monkeypatch.delenv("GIT_NO_LAZY_FETCH")
    before = tree_digest(repo)

    r = fleet_report.repo_report(repo)

    assert tree_digest(repo) == before
    assert r["error"]


def test_eli1043_f2_a_failing_git_status_is_an_error_not_clean(tmp_path):
    repo = tmp_path / "badindex"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "a.txt")
    (repo / "untracked.txt").write_text("u")
    (repo / ".git" / "index").write_bytes(b"not an index")

    r = fleet_report.repo_report(repo)

    assert r["error"]


def test_eli1043_f2_a_failing_rev_list_is_an_error_not_clean(tmp_path):
    repo = tmp_path / "noparent"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "a.txt")
    first = git(repo, "rev-parse", "HEAD")
    commit(repo, "b.txt")
    (repo / ".git" / "objects" / first[:2] / first[2:]).unlink()

    r = fleet_report.repo_report(repo)

    assert r["error"]


def test_eli1043_f2_an_empty_git_dir_inside_another_repo_is_an_error(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    git(parent, "init", "-q")
    commit(parent, "a.txt")
    child = parent / "child"
    (child / ".git").mkdir(parents=True)

    r = fleet_report.repo_report(child)

    assert r["error"]


def test_eli1043_f3_a_password_with_a_literal_at_sign_is_not_printed(tmp_path):
    repo = tmp_path / "cred2"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://user:p@ss@github.com/o/r.git")

    r = fleet_report.repo_report(repo)

    assert r["origin"] == "https://github.com/o/r.git"


def test_eli1043_f4_a_dangling_origin_head_falls_back_to_a_real_default(tmp_path):
    remote = bare_remote(tmp_path, branch="master")
    seed = clone(remote, tmp_path / "seed3")
    git(seed, "switch", "-q", "-c", "main")
    commit(seed, "main-only.txt")
    git(seed, "push", "-q", "origin", "main")
    repo = clone(remote, tmp_path / "home" / "dangle")
    git(repo, "branch", "merged-old")
    git(repo, "update-ref", "-d", "refs/remotes/origin/master")

    r = fleet_report.repo_report(repo)

    assert r["default"] == "origin/main"
    assert "merged-old" in r["stale_merged"]


def test_eli1043_f5_local_only_commits_on_another_branch_are_counted(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "side")
    git(repo, "switch", "-q", "-c", "feature")
    commit(repo, "only-here.txt")
    git(repo, "switch", "-q", "master")

    r = fleet_report.repo_report(repo)

    assert r["no_remote"] == 1


@pytest.mark.parametrize("a, b", [
    ("ssh://git@github.com:22/o/r.git", "git@github.com:o/r.git"),
    ("https://github.com:443/o/r.git", "https://github.com/o/r"),
])
def test_eli1043_f6_default_ports_normalize_away(a, b):
    assert fleet_report.normalize_origin(a) == fleet_report.normalize_origin(b)


def test_eli1043_f7_a_missing_root_exits_2(tmp_path):
    missing = tmp_path / "no-such-dir"

    with pytest.raises(SystemExit) as exc:
        fleet_report.main(["--root", str(missing), "--home", str(missing)])

    assert exc.value.code == 2


# Regression tests for the ELI-1043 adversarial review of the fix.

def test_eli1043_r1_a_folder_name_ending_in_a_space_is_not_a_false_error(tmp_path):
    repo = tmp_path / "sp "
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "a.txt")

    r = fleet_report.repo_report(repo)

    assert r["error"] is None
    assert r["no_remote"] == 1


def test_eli1043_r2_a_missing_default_home_does_not_block_an_explicit_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nonexistent"))

    assert fleet_report.main(["--root", str(root)]) == 0


@pytest.mark.parametrize("a, b", [
    ("http://h.com:80/o/r", "http://h.com/o/r"),
    ("git://h.com:9418/o/r", "git://h.com/o/r"),
    ("git+ssh://git@h.com:22/o/r", "git@h.com:o/r"),
])
def test_eli1043_r4_more_default_ports_normalize_away(a, b):
    assert fleet_report.normalize_origin(a) == fleet_report.normalize_origin(b)


# Regression tests for the ELI-1043 interrogate findings at dbcdbd9.

def test_eli1043_i1_a_pruned_worktree_branch_still_counts_in_the_main_row(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "prune")
    wt = tmp_path / "home" / "gone-wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "wip")
    commit(wt, "w.txt")
    import shutil
    shutil.rmtree(str(wt))

    assert fleet_report.repo_report(repo)["no_remote"] == 1


def test_eli1043_i2_a_worktree_outside_the_scan_root_still_counts_in_the_main_row(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "outside")
    wt = tmp_path / "elsewhere" / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feat")
    commit(wt, "f.txt")

    reports = fleet_report.build_report([tmp_path / "home"], tmp_path / "home")["repos"]

    assert [r["no_remote"] for r in reports] == [1]


def test_eli1043_i3_main_row_counts_every_branch_and_a_linked_row_its_own_head(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "dbl")
    commit(repo, "shared.txt")
    wt = tmp_path / "home" / "dbl-wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "feat")
    commit(wt, "only-wt.txt")

    assert fleet_report.repo_report(repo)["no_remote"] == 2
    assert fleet_report.repo_report(wt)["no_remote"] == 2


def _bare_layout(tmp_path):
    remote = bare_remote(tmp_path)
    proj = tmp_path / "home" / "proj"
    proj.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(remote), str(proj / ".bare")], check=True)
    (proj / ".git").write_text("gitdir: ./.bare\n")
    git(proj, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(proj, "fetch", "-q", "origin")
    git(proj, "worktree", "add", "-q", str(proj / "master-wt"), "master")
    return proj


def test_eli1043_i4_a_bare_repo_with_worktrees_layout_is_not_a_false_error(tmp_path):
    proj = _bare_layout(tmp_path)

    r = fleet_report.repo_report(proj)

    assert r["error"] is None


def test_eli1043_i4_a_side_branch_in_the_bare_layout_is_counted(tmp_path):
    proj = _bare_layout(tmp_path)
    wt = proj / "master-wt"
    git(wt, "switch", "-q", "-c", "side")
    commit(wt, "side.txt")
    git(wt, "switch", "-q", "master")

    assert fleet_report.repo_report(proj)["no_remote"] == 1


def test_eli1043_i5_a_dangling_origin_head_with_no_fallback_is_an_error(tmp_path):
    remote = bare_remote(tmp_path, branch="trunk")
    repo = clone(remote, tmp_path / "home" / "trunk")
    git(repo, "branch", "merged-old")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")

    r = fleet_report.repo_report(repo)

    assert r["error"]


def test_eli1043_i6_a_password_with_a_slash_is_not_printed(tmp_path):
    repo = tmp_path / "cred3"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://user:to/ken@github.com/o/r.git")

    r = fleet_report.repo_report(repo)

    assert "ken" not in r["origin"]


def test_eli1043_s1_a_credential_in_a_transport_prefixed_origin_is_not_printed(tmp_path):
    repo = tmp_path / "cred4"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https::https://user:SECRET2@github.com/o/r.git")

    r = fleet_report.repo_report(repo)

    assert r["origin"] == "https::https://github.com/o/r.git"


def test_eli1043_s2_an_inherited_git_dir_does_not_redirect_the_scan(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    commit(other, "a.txt")
    (other / "s.txt").write_text("s")
    git(other, "add", "s.txt")
    git(other, "stash", "push", "-q")
    repo = tmp_path / "mine"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "b.txt")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    r = fleet_report.repo_report(repo)

    assert r["error"] is None
    assert r["stashes"] == 0
    assert r["no_remote"] == 1


def test_eli1043_s3_untracked_files_count_even_when_status_hides_them(tmp_path):
    repo = tmp_path / "hideuntracked"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "a.txt")
    git(repo, "config", "status.showUntrackedFiles", "no")
    (repo / "notes.txt").write_text("only here")

    assert fleet_report.repo_report(repo)["dirty"] == 1


# Regression tests for the ELI-1043 interrogate round 2 findings at 4cfbe55.

def test_eli1043_j1_a_local_branch_named_like_the_default_does_not_shadow_it(tmp_path):
    remote = bare_remote(tmp_path, branch="main")
    repo = clone(remote, tmp_path / "home" / "amb")
    commit(repo, "unpushed.txt")
    git(repo, "branch", "origin/main")
    git(repo, "branch", "feat")

    r = fleet_report.repo_report(repo)

    assert r["stale_merged"] == []


def test_eli1043_j2_a_normal_clone_with_core_bare_true_is_an_error(tmp_path):
    repo = tmp_path / "corebare"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "a.txt")
    (repo / "untracked.txt").write_text("u")
    git(repo, "config", "core.bare", "true")

    assert fleet_report.repo_report(repo)["error"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_eli1043_j3_a_fifo_named_git_does_not_hang_the_scan(tmp_path):
    home = tmp_path / "home"
    (home / "fifo").mkdir(parents=True)
    os.mkfifo(str(home / "fifo" / ".git"))
    out = subprocess.run([sys.executable, str(SKILL / "scripts" / "fleet_report.py"),
                          "--root", str(home), "--home", str(home), "--json"],
                         capture_output=True, text=True, timeout=30)

    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["repos"][0]["error"]


def _side_commit(wt):
    git(wt, "switch", "-q", "-c", "side")
    commit(wt, "side.txt")
    git(wt, "switch", "-q", "-")


def test_eli1043_k1_a_separate_git_dir_clone_counts_side_branches(tmp_path):
    remote = bare_remote(tmp_path)
    repo = tmp_path / "home" / "sep"
    subprocess.run(["git", "clone", "-q", "--separate-git-dir", str(tmp_path / "sep.git"),
                    str(remote), str(repo)], check=True)
    _side_commit(repo)

    assert fleet_report.repo_report(repo)["no_remote"] == 1


def test_eli1043_k2_a_submodule_counts_side_branches(tmp_path):
    remote = bare_remote(tmp_path)
    sup = tmp_path / "home" / "super"
    sup.mkdir(parents=True)
    git(sup, "init", "-q")
    subprocess.run(["git", "-C", str(sup), "-c", "protocol.file.allow=always", "submodule",
                    "add", "-q", str(remote), "sub"], check=True)
    _side_commit(sup / "sub")

    assert fleet_report.repo_report(sup / "sub")["no_remote"] == 1


def test_eli1043_k3_a_worktree_of_a_bare_repo_counts_side_branches(tmp_path):
    remote = bare_remote(tmp_path)
    proj = tmp_path / "home" / "proj"
    proj.mkdir(parents=True)
    bare = proj / "repo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(remote), str(bare)], check=True)
    git(bare, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(bare, "fetch", "-q", "origin")
    git(bare, "worktree", "add", "-q", str(proj / "main"), "master")
    _side_commit(proj / "main")

    reports = fleet_report.build_report([proj], proj)["repos"]

    assert sum(r["no_remote"] for r in reports) >= 1


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repo@v2.git",
    "https://bitbucket.example.com/scm/~alice@corp.com/tools.git",
])
def test_eli1043_k4_an_at_sign_in_the_path_is_kept(tmp_path, url):
    repo = tmp_path / "atpath"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", url)

    assert fleet_report.repo_report(repo)["origin"] == url


# Regression tests for the ELI-1043 interrogate round 3 findings at ac086eb.

def test_eli1043_l1_a_tag_named_like_the_default_branch_is_not_stale(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "tagged")
    git(repo, "switch", "-q", "-c", "feat")
    git(repo, "tag", "master")

    assert fleet_report.repo_report(repo)["stale_merged"] == []


def test_eli1043_l1_a_tag_named_like_a_worktree_branch_is_not_stale(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "tagged2")
    git(repo, "worktree", "add", "-q", str(tmp_path / "home" / "wt"), "-b", "side")
    git(repo, "tag", "side")

    assert fleet_report.repo_report(repo)["stale_merged"] == []


def test_eli1043_l2_an_unparsable_port_hides_the_whole_password():
    assert fleet_report._redact("https://user:to/k@en@github.com/o/r.git") == "https://github.com/o/r.git"


def test_eli1043_l1_a_tag_named_like_the_current_branch_keeps_it_out_of_stale(tmp_path):
    remote = bare_remote(tmp_path)
    repo = clone(remote, tmp_path / "home" / "tagged3")
    git(repo, "switch", "-q", "-c", "feat")
    git(repo, "tag", "feat")

    r = fleet_report.repo_report(repo)

    assert r["branch"] == "feat"
    assert r["stale_merged"] == []


def test_eli1043_m1_status_does_not_run_a_configured_clean_filter(tmp_path):
    repo = tmp_path / "filtered"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitattributes").write_text("*.bin filter=probe\n")
    (repo / "a.bin").write_text("x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "a")
    marker = tmp_path / "filter-ran"
    git(repo, "config", "filter.probe.clean", "touch '%s'; cat" % marker.as_posix())
    git(repo, "config", "filter.probe.required", "true")
    os.utime(str(repo / "a.bin"), (1, 1))

    r = fleet_report.repo_report(repo)

    assert not marker.exists()
    assert r["error"] is None


def test_eli1043_m3_an_ipv6_host_with_an_at_sign_in_the_path_is_kept():
    url = "https://[2001:db8::1]/team-a/tool@v2.git"
    assert fleet_report._redact(url) == url


# Regression tests for the ELI-1043 interrogate round 4 findings at 426cd70.

def _probe_filter(repo, driver, marker):
    """Commit a.bin under filter=<driver>, then configure that driver to touch marker."""
    with open(str(repo / ".gitattributes"), "a") as f:
        f.write("*.bin filter=%s\n" % driver)
    (repo / "a.bin").write_text("x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "probe")
    with open(str(repo / ".git" / "config"), "a") as f:
        f.write('[filter "%s"]\n\tclean = touch \'%s\'; cat\n\trequired = true\n'
                % (driver, marker.as_posix()))
    os.utime(str(repo / "a.bin"), (1, 1))


def test_eli1043_n1_a_submodule_filter_does_not_run_from_the_parent_row(tmp_path):
    src = tmp_path / "subsrc"
    src.mkdir()
    git(src, "init", "-q")
    (src / ".gitattributes").write_text("*.bin filter=subprobe\n")
    (src / "a.bin").write_text("x\n")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "a")
    sup = tmp_path / "home" / "super"
    sup.mkdir(parents=True)
    git(sup, "init", "-q")
    commit(sup, "README")
    git(sup, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "sub")
    git(sup, "commit", "-q", "-m", "sub")
    marker = tmp_path / "sub-filter-ran"
    git(sup / "sub", "config", "filter.subprobe.clean", "touch '%s'; cat" % marker.as_posix())
    os.utime(str(sup / "sub" / "a.bin"), (1, 1))

    r = fleet_report.repo_report(sup)

    assert not marker.exists()
    assert r["error"] is None


def test_eli1043_n2_a_driver_name_with_an_equals_sign_is_blanked(tmp_path):
    repo = tmp_path / "eq"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "seed.txt")
    marker = tmp_path / "eq-filter-ran"
    _probe_filter(repo, "eq=sign", marker)

    r = fleet_report.repo_report(repo)

    assert not marker.exists()
    assert r["error"] is None
