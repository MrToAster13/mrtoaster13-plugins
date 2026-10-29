"""Tests for factory's prerequisite check.

Each test builds a clean profile in a temp directory (an empty HOME, an empty
project and a PATH that holds only what the test puts there), so the result
never depends on what the machine running the suite has installed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[1]
PLUGIN = FACTORY.parents[1]
REPO = PLUGIN.parents[1]
PREFLIGHT = FACTORY / "scripts" / "preflight.py"

sys.path.insert(0, str(FACTORY / "scripts"))

import preflight  # noqa: E402

SKILLS = ("setup-matt-pocock-skills", "to-spec", "to-tickets", "tdd")


def make_skill(root, name):
    path = Path(root) / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: {}\n---\n".format(name), encoding="utf-8")


def make_tool(bindir, name):
    bindir.mkdir(parents=True, exist_ok=True)
    tool = bindir / name
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)


@pytest.fixture
def profile(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    bindir = tmp_path / "bin"
    for d in (home, project, bindir):
        d.mkdir()
    return home, project, bindir


# A Windows child Python cannot start without SYSTEMROOT ("failed to get random
# numbers"). It is the only parent variable that passes through. PATHEXT stays
# out: the child does not need it, and on Python 3.8 to 3.11 setting it makes
# shutil.which skip the extensionless fake tools. (On Windows with 3.12+,
# shutil.which skips them either way, so those tests still fail there.)
PASSTHROUGH = ("SYSTEMROOT",)


def run(home, project, bindir, *args, vault_env=None):
    env = {"HOME": str(home), "USERPROFILE": str(home), "PATH": str(bindir)}
    for name in PASSTHROUGH:
        if name in os.environ:
            env[name] = os.environ[name]
    if vault_env is not None:
        env["FACTORY_VAULT"] = str(vault_env)
    return subprocess.run(
        [sys.executable, "-I", str(PREFLIGHT), *args],
        cwd=str(project), env=env, capture_output=True, text=True, timeout=60)


def install_everything(home, bindir):
    for name in SKILLS:
        make_skill(home / ".claude" / "skills", name)
    ticket = home / "obsidian-vault" / "Tickets" / "_tools" / "ticket.py"
    ticket.parent.mkdir(parents=True)
    ticket.write_text("", encoding="utf-8")
    make_tool(bindir, "git")
    make_tool(bindir, "gh")


def test_clean_profile_stops_and_names_every_missing_prerequisite(profile):
    home, project, bindir = profile
    result = run(home, project, bindir)
    assert result.returncode == 1
    for name in SKILLS:
        assert re.search(r"missing: skill {}\b".format(re.escape(name)), result.stdout), result.stdout
    assert "missing: ticket.py" in result.stdout
    assert "missing: tool git" in result.stdout
    assert "missing: tool gh" in result.stdout


def test_missing_message_says_where_to_get_each_item(profile):
    home, project, bindir = profile
    result = run(home, project, bindir)
    assert "github.com/mattpocock/skills" in result.stdout
    assert "FACTORY_VAULT" in result.stdout
    assert "cli.github.com" in result.stdout


def test_full_profile_passes(profile):
    home, project, bindir = profile
    install_everything(home, bindir)
    result = run(home, project, bindir)
    assert result.returncode == 0, result.stdout
    assert "missing" not in result.stdout


def test_one_missing_skill_is_the_only_one_named(profile):
    home, project, bindir = profile
    install_everything(home, bindir)
    (home / ".claude" / "skills" / "to-tickets" / "SKILL.md").unlink()
    result = run(home, project, bindir)
    assert result.returncode == 1
    missing = [line for line in result.stdout.splitlines() if "missing:" in line]
    assert len(missing) == 1, result.stdout
    assert "skill to-tickets" in missing[0]


def test_skills_from_a_plugin_install_count(profile):
    home, project, bindir = profile
    install_everything(home, bindir)
    for name in SKILLS:
        (home / ".claude" / "skills" / name / "SKILL.md").unlink()
        make_skill(home / ".claude" / "plugins" / "cache" / "mp" / "mattpocock-skills" / "1.0" / "skills" / "engineering", name)
    result = run(home, project, bindir)
    assert result.returncode == 0, result.stdout


def test_skills_in_the_project_count(profile):
    home, project, bindir = profile
    install_everything(home, bindir)
    for name in SKILLS:
        (home / ".claude" / "skills" / name / "SKILL.md").unlink()
        make_skill(project / ".claude" / "skills", name)
    result = run(home, project, bindir)
    assert result.returncode == 0, result.stdout


def test_vault_comes_from_the_flag_or_the_environment(profile, tmp_path):
    home, project, bindir = profile
    install_everything(home, bindir)
    other = tmp_path / "elsewhere"
    (home / "obsidian-vault" / "Tickets" / "_tools" / "ticket.py").unlink()
    assert run(home, project, bindir).returncode == 1
    ticket = other / "Tickets" / "_tools" / "ticket.py"
    ticket.parent.mkdir(parents=True)
    ticket.write_text("", encoding="utf-8")
    assert run(home, project, bindir, "--vault", str(other)).returncode == 0
    assert run(home, project, bindir, vault_env=other).returncode == 0


def captured_child_env(monkeypatch, profile, **kwargs):
    seen = {}

    def fake_run(cmd, **options):
        seen.update(options["env"])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run(*profile, **kwargs)
    return seen


def test_run_passes_systemroot_so_a_windows_child_python_can_start(monkeypatch, profile):
    # Without SYSTEMROOT a Windows child Python dies with "failed to get
    # random numbers". The sentinel proves the value comes from os.environ.
    monkeypatch.setenv("SYSTEMROOT", "sentinel-systemroot")
    env = captured_child_env(monkeypatch, profile)
    assert env.get("SYSTEMROOT") == "sentinel-systemroot"


def test_run_leaks_nothing_else_from_the_parent(monkeypatch, profile):
    monkeypatch.setenv("SYSTEMROOT", "sentinel-systemroot")
    monkeypatch.setenv("FACTORY_LEAK_CHECK", "leaked")
    monkeypatch.setenv("FACTORY_VAULT", "leaked-vault")
    monkeypatch.setenv("PATHEXT", ".EXE")
    env = captured_child_env(monkeypatch, profile)
    assert set(env) == {"HOME", "USERPROFILE", "PATH", "SYSTEMROOT"}
    home, _, bindir = profile
    assert env["PATH"] == str(bindir)
    assert env["HOME"] == env["USERPROFILE"] == str(home)


def test_run_omits_systemroot_when_the_parent_has_none(monkeypatch, profile, tmp_path):
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    env = captured_child_env(monkeypatch, profile, vault_env=tmp_path)
    assert set(env) == {"HOME", "USERPROFILE", "PATH", "FACTORY_VAULT"}


def test_skill_md_runs_the_preflight_before_binding():
    text = (FACTORY / "SKILL.md").read_text(encoding="utf-8")
    phase0 = text.index("## Phase 0")
    check = text.find("scripts/preflight.py", phase0)
    repo_step = text.index("**Repo.**", phase0)
    assert check != -1 and check < repo_step


@pytest.mark.parametrize("readme", [REPO / "README.md", PLUGIN / "README.md"])
def test_readmes_name_every_prerequisite_and_its_source(readme):
    text = readme.read_text(encoding="utf-8")
    for item in preflight.PREREQUISITES:
        assert item.name in text, "{} does not name {}".format(readme, item.name)
        assert item.source_hint in text, "{} does not say where to get {}".format(readme, item.name)
