"""The marketplace manifests, skill frontmatter and plugin README must agree
with the folders on disk (ELI-892)."""

import json
import pathlib
import re
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^### `/([a-z0-9-]+)`", re.M)


def frontmatter(text):
    """Return the key: value pairs of a SKILL.md frontmatter block, or None."""
    lines = text.lstrip("﻿").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    fields = {}
    block = None
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if block and line.startswith((" ", "\t")):
            fields[block] = (fields[block] + " " + line.strip()).strip()
            continue
        block = None
        key, sep, value = line.partition(":")
        if sep and key and not key.startswith((" ", "\t")):
            value = value.strip()
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                block, value = key.strip(), ""
            fields[key.strip()] = value.strip("\"'")
    return None


def manifest_problems(root):
    problems = []
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    if not market.get("plugins"):
        problems.append("marketplace.json has no plugins list")
    for entry in market.get("plugins") or []:
        name = entry.get("name")
        source = (root / entry.get("source", "")).resolve()
        manifest = source / ".claude-plugin" / "plugin.json"
        if not source.is_dir() or not manifest.is_file():
            problems.append("{}: source {} has no .claude-plugin/plugin.json".format(name, entry.get("source")))
            continue
        plugin = json.loads(manifest.read_text("utf-8"))
        if plugin.get("name") != name:
            problems.append("{}: plugin.json name is {!r}".format(name, plugin.get("name")))
    return problems


def skill_problems(root):
    problems = []
    skill_dirs = [d for d in sorted((root / "plugins").glob("*/skills/*")) if d.is_dir()]
    if not skill_dirs:
        problems.append("no plugins/*/skills/* folders found")
    for skill_dir in skill_dirs:
        rel = skill_dir.relative_to(root).as_posix()
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            problems.append("{}: no SKILL.md".format(rel))
            continue
        fields = frontmatter(skill_md.read_text("utf-8"))
        if fields is None:
            problems.append("{}: SKILL.md has no frontmatter".format(rel))
            continue
        if fields.get("name") != skill_dir.name:
            problems.append("{}: frontmatter name is {!r}".format(rel, fields.get("name")))
        if not fields.get("description"):
            problems.append("{}: frontmatter has no description".format(rel))
    return problems


def readme_problems(root, plugin="autonomous-workflows"):
    base = root / "plugins" / plugin
    listed = set(HEADING.findall((base / "README.md").read_text("utf-8")))
    on_disk = {p.name for p in (base / "skills").iterdir() if p.is_dir()}
    problems = []
    for name in sorted(on_disk - listed):
        problems.append("{} README has no ### `/{}` heading".format(plugin, name))
    for name in sorted(listed - on_disk):
        problems.append("{} README lists /{} but skills/{} does not exist".format(plugin, name, name))
    return problems


def copy_repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(str(ROOT / ".claude-plugin"), str(dest / ".claude-plugin"))
    shutil.copytree(str(ROOT / "plugins"), str(dest / "plugins"),
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
    return dest


def test_the_repo_has_no_manifest_skill_or_readme_problems():
    assert manifest_problems(ROOT) + skill_problems(ROOT) + readme_problems(ROOT) == []


def test_a_marketplace_source_without_plugin_json_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    (root / "plugins" / "clear-not-compact" / ".claude-plugin" / "plugin.json").unlink()
    assert any("clear-not-compact" in p for p in manifest_problems(root))


def test_a_plugin_name_mismatch_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    path = root / "plugins" / "autonomous-workflows" / ".claude-plugin" / "plugin.json"
    data = json.loads(path.read_text("utf-8"))
    data["name"] = "renamed"
    path.write_text(json.dumps(data), "utf-8")
    assert any("renamed" in p for p in manifest_problems(root))


def test_a_skill_whose_name_differs_from_its_folder_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    path = root / "plugins" / "autonomous-workflows" / "skills" / "tdd-loop" / "SKILL.md"
    path.write_text(path.read_text("utf-8").replace("name: tdd-loop", "name: tdd-loopy", 1), "utf-8")
    assert any("tdd-loopy" in p for p in skill_problems(root))


def test_a_skill_without_description_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    path = root / "plugins" / "autonomous-workflows" / "skills" / "remediate" / "SKILL.md"
    path.write_text("---\nname: remediate\n---\nbody\n", "utf-8")
    assert any("remediate: frontmatter has no description" in p for p in skill_problems(root))


def test_a_skill_folder_missing_from_the_readme_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    new = root / "plugins" / "autonomous-workflows" / "skills" / "new-skill"
    new.mkdir()
    (new / "SKILL.md").write_text("---\nname: new-skill\ndescription: x\n---\n", "utf-8")
    assert any("/new-skill" in p for p in readme_problems(root))


def test_a_readme_heading_without_a_folder_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    shutil.rmtree(str(root / "plugins" / "autonomous-workflows" / "skills" / "remediate"))
    assert any("skills/remediate does not exist" in p for p in readme_problems(root))


# Regression cases from the adversarial review.

def test_an_empty_or_misnamed_plugins_list_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    path = root / ".claude-plugin" / "marketplace.json"
    data = json.loads(path.read_text("utf-8"))
    data["plugin"] = data.pop("plugins")
    path.write_text(json.dumps(data), "utf-8")
    assert manifest_problems(root)
    data["plugins"] = []
    path.write_text(json.dumps(data), "utf-8")
    assert manifest_problems(root)


def test_no_skill_folders_at_all_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    shutil.rmtree(str(root / "plugins"))
    (root / "plugins").mkdir()
    assert skill_problems(root)


def test_a_folded_description_with_no_text_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    path = root / "plugins" / "autonomous-workflows" / "skills" / "remediate" / "SKILL.md"
    path.write_text("---\nname: remediate\ndescription: >\n---\nbody\n", "utf-8")
    assert any("remediate: frontmatter has no description" in p for p in skill_problems(root))


def test_a_folded_description_with_text_is_accepted():
    fields = frontmatter("---\nname: x\ndescription: >\n  folded text\n  more\n---\n")
    assert fields["description"] == "folded text more"


def test_a_byte_order_mark_does_not_hide_the_frontmatter():
    assert frontmatter("\ufeff---\nname: x\ndescription: y\n---\n") == {"name": "x", "description": "y"}
