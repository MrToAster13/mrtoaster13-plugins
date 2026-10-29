"""Check that everything factory needs and does not ship is installed.

factory calls skills from Matt Pocock's skill set, a ticket.py tracker inside
an Obsidian vault, git and the GitHub CLI. None of them ship with this plugin.
Run this before Phase 0 so a missing piece stops the run at the start instead
of halfway through.

    python preflight.py [--vault PATH]

Exit 0 when everything is found. Exit 1 after printing one "missing:" line per
absent item, each with where to get it. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections import namedtuple
from pathlib import Path

MATT_POCOCK = ("Install Matt Pocock's skills, https://github.com/mattpocock/skills "
               "(claude plugin install mattpocock-skills@claude-plugins-official)")

Prerequisite = namedtuple("Prerequisite", "kind name source_hint where")

PREREQUISITES = (
    Prerequisite("skill", "setup-matt-pocock-skills", "github.com/mattpocock/skills", MATT_POCOCK),
    Prerequisite("skill", "to-spec", "github.com/mattpocock/skills", MATT_POCOCK),
    Prerequisite("skill", "to-tickets", "github.com/mattpocock/skills", MATT_POCOCK),
    Prerequisite("skill", "tdd", "github.com/mattpocock/skills", MATT_POCOCK),
    Prerequisite("ticket", "ticket.py", "FACTORY_VAULT",
                 "factory needs an Obsidian vault with a ticket.py tracker, which is not published. "
                 "See the plugin README. Point at the vault with --vault PATH or FACTORY_VAULT; "
                 "the default is ~/obsidian-vault"),
    Prerequisite("tool", "git", "git-scm.com", "Install it from https://git-scm.com"),
    Prerequisite("tool", "gh", "cli.github.com", "Install it from https://cli.github.com"),
)


def has_skill(name, home, project):
    """True if a skill folder of this name is in the project or user skill
    folder, or anywhere under a skills folder of an installed plugin."""
    for root in (Path(project) / ".claude" / "skills", Path(home) / ".claude" / "skills"):
        if (root / name / "SKILL.md").is_file():
            return True
    plugins = Path(home) / ".claude" / "plugins"
    if plugins.is_dir():
        for found in plugins.rglob("SKILL.md"):
            if found.parent.name == name and "skills" in found.parts:
                return True
    return False


def ticket_tool(vault):
    return Path(vault).expanduser() / "Tickets" / "_tools" / "ticket.py"


def find_missing(home, project, vault):
    missing = []
    for item in PREREQUISITES:
        if item.kind == "skill":
            ok = has_skill(item.name, home, project)
        elif item.kind == "ticket":
            ok = ticket_tool(vault).is_file()
        else:
            ok = shutil.which(item.name) is not None
        if not ok:
            missing.append(item)
    return missing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", help="Obsidian vault holding Tickets/_tools/ticket.py")
    args = parser.parse_args(argv)
    home = Path.home()
    vault = args.vault or os.environ.get("FACTORY_VAULT") or str(home / "obsidian-vault")
    missing = find_missing(home, Path.cwd(), vault)
    if not missing:
        print("factory preflight: every prerequisite found")
        return 0
    for item in missing:
        label = "ticket.py at {}".format(ticket_tool(vault)) if item.kind == "ticket" else \
            "{} {}".format(item.kind, item.name)
        print("missing: {}. {}".format(label, item.where))
    print("factory preflight: {} prerequisite(s) missing. factory stops here.".format(len(missing)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
