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
    listed = {(root / entry.get("source", "")).resolve() for entry in market.get("plugins") or []}
    for base in sorted((root / "plugins").glob("*")):
        if (base / ".claude-plugin" / "plugin.json").is_file() and base.resolve() not in listed:
            problems.append("{}: plugins/{} has no marketplace.json entry".format(base.name, base.name))
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


BULLET = re.compile(r"^- \*\*`/([a-z0-9-]+)`\*\*", re.M)
SLASH = re.compile(r"(?<![\w/.:-])/([a-z][a-z0-9-]*)")
INSTALL = re.compile(r"^\s*(cp|Copy-Item)\b.*$", re.M)
COUNT = re.compile(r"\b([A-Za-z]+|[0-9]+) skills\b")
NUMBERS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    " fifteen sixteen seventeen eighteen nineteen twenty".split())}


def skills_on_disk(root):
    """Map each plugin folder that has skills/ to the set of its skill names."""
    return {
        base.name: {p.name for p in (base / "skills").iterdir() if p.is_dir()}
        for base in sorted((root / "plugins").glob("*"))
        if (base / "skills").is_dir()
    }


def layout_names(readme, plugin):
    """Slash names in the root README layout-tree comment for a plugin, or None."""
    lines = readme.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^[\s│]*[├└]── " + re.escape(plugin) + r"/\s+#(.*)$", line)
        if not m:
            continue
        comment = m.group(1)
        for more in lines[i + 1:]:
            cont = re.match(r"^[\s│]*#(.*)$", more)
            if not cont:
                break
            comment += " " + cont.group(1)
        return set(SLASH.findall(comment))
    return None


def root_section(readme, plugin):
    """The root README section headed ### [`plugin`], or None."""
    m = re.search(r"^### \[`" + re.escape(plugin) + r"`\].*?(?=^#{1,3} |\Z)", readme, re.M | re.S)
    return m.group(0) if m else None


def skill_listings(root, plugin):
    """Yield (where, names) for every place that lists a plugin's skills."""
    base = root / "plugins" / plugin
    rel = "plugins/{}".format(plugin)
    readme_path = base / "README.md"
    if readme_path.is_file():
        readme = readme_path.read_text("utf-8")
        yield ("{}/README.md headings and bullets".format(rel),
               set(HEADING.findall(readme)) | set(BULLET.findall(readme)))
        installs = [m for m in INSTALL.finditer(readme) if "skills/" in m.group(0)]
        for command in ("cp", "Copy-Item"):
            lines = [m.group(0) for m in installs if m.group(1) == command] or [""]
            for line in lines:
                yield ("{}/README.md {} line".format(rel, command),
                       set(re.findall(r"skills/([a-z0-9-]+)", line)))
        yield ("{}/README.md namespaced list".format(rel),
               set(re.findall(r"`/" + re.escape(plugin) + r":([a-z0-9-]+)`", readme)))
    manifest = base / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        description = json.loads(manifest.read_text("utf-8")).get("description", "")
        yield ("{}/.claude-plugin/plugin.json description".format(rel), set(SLASH.findall(description)))
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    for entry in market.get("plugins") or []:
        if entry.get("name") == plugin:
            yield ("marketplace.json {} description".format(plugin),
                   set(SLASH.findall(entry.get("description", ""))))
    root_readme = root / "README.md"
    if root_readme.is_file():
        text = root_readme.read_text("utf-8")
        names = layout_names(text, plugin)
        if names is not None:
            yield ("README.md layout tree", names)
        section = root_section(text, plugin) or ""
        bullets = set(BULLET.findall(section))
        if bullets:
            yield ("README.md {} section bullets".format(plugin), bullets)


def catalog_problems(root):
    """Report every skill list that disagrees with the skill folders (ELI-1084)."""
    problems = []
    root_readme = root / "README.md"
    text = root_readme.read_text("utf-8") if root_readme.is_file() else None
    if text is None:
        problems.append("README.md is missing")
    for plugin, on_disk in skills_on_disk(root).items():
        if not (root / "plugins" / plugin / "README.md").is_file():
            problems.append("plugins/{}/README.md is missing".format(plugin))
        for where, listed in skill_listings(root, plugin):
            for name in sorted(on_disk - listed):
                problems.append("{} does not list /{}".format(where, name))
            for name in sorted(listed - on_disk):
                problems.append("{} lists /{} but plugins/{}/skills/{} does not exist".format(
                    where, name, plugin, name))
        if text is None:
            continue
        if layout_names(text, plugin) is None:
            problems.append("README.md layout tree has no line for {}/".format(plugin))
        section = root_section(text, plugin)
        if section is None:
            problems.append("README.md has no ### [`{}`] section".format(plugin))
            continue
        counts = [(w, int(w) if w.isdigit() else NUMBERS.get(w.lower())) for w in COUNT.findall(section)]
        for word, count in [c for c in counts if c[1] is not None][:1]:
            if count != len(on_disk):
                problems.append("README.md {} section says {} skills, plugins/{}/skills has {}".format(
                    plugin, word, plugin, len(on_disk)))
    return problems


def copy_repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(str(ROOT / ".claude-plugin"), str(dest / ".claude-plugin"))
    shutil.copy(str(ROOT / "README.md"), str(dest / "README.md"))
    shutil.copytree(str(ROOT / "plugins"), str(dest / "plugins"),
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
    return dest


def test_the_repo_has_no_manifest_skill_or_catalog_problems():
    assert manifest_problems(ROOT) + skill_problems(ROOT) + catalog_problems(ROOT) == []


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
    assert any("/new-skill" in p for p in catalog_problems(root))


def test_a_readme_heading_without_a_folder_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    shutil.rmtree(str(root / "plugins" / "autonomous-workflows" / "skills" / "remediate"))
    assert any("skills/remediate does not exist" in p for p in catalog_problems(root))


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


# ELI-1084: every place that lists a plugin's skills must match the folders.

def edit(path, old, new):
    text = path.read_text("utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), "utf-8")


def edit_json(path, change):
    data = json.loads(path.read_text("utf-8"))
    change(data)
    path.write_text(json.dumps(data), "utf-8")


def test_the_repo_has_no_catalog_problems():
    assert catalog_problems(ROOT) == []


def test_a_skill_missing_from_the_clear_not_compact_readme_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "plugins" / "clear-not-compact" / "README.md", "- **`/resume`**", "- **`/resumes`**")
    problems = catalog_problems(root)
    assert any("clear-not-compact" in p and "/resume" in p and "README.md" in p for p in problems)
    assert any("/resumes" in p for p in problems)


def test_a_skill_missing_from_a_plugin_json_description_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    path = root / "plugins" / "autonomous-workflows" / ".claude-plugin" / "plugin.json"
    edit_json(path, lambda d: d.update(description=d["description"].replace("/batch-run", "/batch-go")))
    problems = catalog_problems(root)
    assert any("plugin.json" in p and "/batch-run" in p for p in problems)
    assert any("plugin.json" in p and "/batch-go" in p for p in problems)


def test_a_skill_missing_from_a_marketplace_description_is_reported(tmp_path):
    root = copy_repo(tmp_path)

    def change(data):
        entry = data["plugins"][0]
        entry["description"] = entry["description"].replace("/resume", "/restore")
    edit_json(root / ".claude-plugin" / "marketplace.json", change)
    problems = catalog_problems(root)
    assert any("marketplace.json" in p and "/resume" in p for p in problems)
    assert any("marketplace.json" in p and "/restore" in p for p in problems)


def test_a_skill_missing_from_an_install_line_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    readme = root / "plugins" / "autonomous-workflows" / "README.md"
    edit(readme, "cp -r skills/factory skills/remediate", "cp -r skills/factory")
    assert any("cp" in p and "/remediate" in p for p in catalog_problems(root))


def test_a_skill_missing_from_a_copy_item_line_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    readme = root / "plugins" / "clear-not-compact" / "README.md"
    edit(readme, "Copy-Item -Recurse skills/handoff, skills/resume", "Copy-Item -Recurse skills/handoff")
    assert any("Copy-Item" in p and "/resume" in p for p in catalog_problems(root))


def test_a_skill_missing_from_the_namespaced_list_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    readme = root / "plugins" / "autonomous-workflows" / "README.md"
    edit(readme, ", `/autonomous-workflows:batch-run`", "")
    assert any("namespaced" in p and "/batch-run" in p for p in catalog_problems(root))


def test_a_skill_missing_from_the_root_layout_tree_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "# /handoff, /resume", "# /handoff")
    assert any("layout" in p and "/resume" in p for p in catalog_problems(root))


def test_a_skill_missing_from_the_root_readme_bullets_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "- **`/batch-run`**", "- **`/batch-walk`**")
    problems = catalog_problems(root)
    assert any("README.md" in p and "/batch-run" in p for p in problems)
    assert any("/batch-walk" in p for p in problems)


def test_a_wrong_skill_count_in_the_root_readme_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "Seven skills", "Six skills")
    assert any("Six" in p and "7" in p for p in catalog_problems(root))


def test_a_plugin_folder_without_a_marketplace_entry_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    edit_json(root / ".claude-plugin" / "marketplace.json", lambda d: d["plugins"].pop(0))
    assert any("clear-not-compact" in p and "marketplace" in p for p in manifest_problems(root))


# Regression cases from the ELI-1084 adversarial review.

def drop_lines(path, prefix):
    text = path.read_text("utf-8")
    kept = [line for line in text.split("\n") if not line.startswith(prefix)]
    assert len(kept) < len(text.split("\n")), prefix
    path.write_text("\n".join(kept), "utf-8")


def test_a_deleted_install_line_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    readme = root / "plugins" / "autonomous-workflows" / "README.md"
    drop_lines(readme, "cp -r skills/")
    drop_lines(readme, "Copy-Item -Recurse skills/")
    problems = catalog_problems(root)
    assert any("cp line" in p and "/factory" in p for p in problems)
    assert any("Copy-Item line" in p and "/factory" in p for p in problems)


def test_a_deleted_namespaced_list_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    readme = root / "plugins" / "autonomous-workflows" / "README.md"
    drop_lines(readme, "Plugin skills are namespaced:")
    assert any("namespaced" in p and "/factory" in p for p in catalog_problems(root))


def test_a_root_readme_section_that_cannot_be_found_is_reported(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "### [`autonomous-workflows`](plugins/autonomous-workflows/)",
         "### `autonomous-workflows`")
    assert any("autonomous-workflows" in p and "section" in p for p in catalog_problems(root))


def test_a_level_four_heading_does_not_end_a_root_readme_section(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "- **`/batch-run`**", "#### More\n\n- **`/batch-walk`**")
    assert any("/batch-walk" in p for p in catalog_problems(root))


def test_a_skill_count_in_digits_is_checked(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "Seven skills", "8 skills")
    assert any("says 8 skills" in p for p in catalog_problems(root))


def test_only_the_first_skill_count_in_a_section_is_checked(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "and `tdd` skills", "and `tdd` skills (four skills)")
    assert catalog_problems(root) == []


def test_a_word_that_is_not_a_number_before_skills_is_not_a_count(tmp_path):
    root = copy_repo(tmp_path)
    edit(root / "README.md", "Seven skills", "Workflow skills, seven skills")
    assert catalog_problems(root) == []
