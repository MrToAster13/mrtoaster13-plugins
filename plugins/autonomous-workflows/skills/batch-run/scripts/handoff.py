"""Render a handoff doc for an unfinished batch, straight from the checkpoint dir.

This runs on the way back up, not on the way down. When a session dies on
limits there is no chance to execute cleanup code, so the checkpoint files are
the real handoff; this script just formats what they already say.

Numbering and location follow the same convention as the `persist` skill:
`<root>/docs/handoffs/NNN-<slug>.md`, where root is the git toplevel if there
is one and the current directory otherwise, and NNN is the numeric maximum of
existing handoffs plus one.

CLI:
    python handoff.py --units units.json --dir checkpoint --slug resumes \
        [--title "Resume batch"] [--resume-command "/batch-run ..."] [--root PATH]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import state

_NUMBERED = re.compile(r"^(\d+)-")


def find_root(explicit=None):
    if explicit:
        return Path(explicit)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        )
        top = out.stdout.decode("utf-8", "replace").strip()
        if top:
            return Path(top)
    except (OSError, subprocess.CalledProcessError):
        pass
    return Path.cwd()


def next_number(handoff_dir):
    highest = 0
    if handoff_dir.is_dir():
        for entry in handoff_dir.iterdir():
            match = _NUMBERED.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return "{:03d}".format(highest + 1)


def render(summary, title, resume_command):
    ids = summary["ids"]
    done = summary["complete"]
    lines = []
    lines.append("# Handoff: {}".format(title))
    lines.append("")
    lines.append("Status: {}".format("ready-to-verify" if done else "in-progress"))
    lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append(
        "Batch of {} units, each written by one agent and checked by a second, "
        "independent agent. A unit counts as done only once that check passes."
        .format(summary["total"])
    )
    lines.append("")
    lines.append("## Done")
    lines.append("")
    lines.append("- {} of {} units verified.".format(summary["verified"], summary["total"]))
    if ids["verified"]:
        lines.append("- Verified: {}".format(", ".join("`{}`".format(i) for i in ids["verified"])))
    lines.append("")
    lines.append("## Next (in order)")
    lines.append("")
    if done:
        lines.append("1. Nothing outstanding. Review the artifacts and ship.")
    else:
        lines.append("1. Re-run the batch with resume. Only unverified units are reprocessed:")
        lines.append("")
        lines.append("   ```")
        lines.append("   {}".format(resume_command))
        lines.append("   ```")
        if summary["failures"]:
            lines.append("")
            lines.append("2. Read the failure reasons below first. A unit that failed "
                         "verification twice usually needs a prompt fix, not a retry.")
    lines.append("")
    lines.append("## Outstanding")
    lines.append("")
    if summary["failures"]:
        lines.append("Failed verification:")
        lines.append("")
        for failure in summary["failures"]:
            lines.append("- `{}` - {}".format(failure["id"], failure["reason"]))
        lines.append("")
    if ids["written"]:
        lines.append("Written but never verified (worker finished, checker did not):")
        lines.append("")
        for unit_id in ids["written"]:
            lines.append("- `{}`".format(unit_id))
        lines.append("")
    if ids["pending"]:
        lines.append("Never started:")
        lines.append("")
        for unit_id in ids["pending"]:
            lines.append("- `{}`".format(unit_id))
        lines.append("")
    if not (summary["failures"] or ids["written"] or ids["pending"]):
        lines.append("Nothing. Every unit is verified.")
        lines.append("")
    lines.append("## Gotchas")
    lines.append("")
    lines.append("- The checkpoint dir is the source of truth, not this file. "
                 "This file is a rendering of it and goes stale the moment the batch runs again.")
    lines.append("- A unit with no record, or with a record that fails to parse, is "
                 "treated as pending. Deleting a record file is a valid way to force a redo.")
    lines.append("")
    return "\n".join(lines)


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--units", required=True)
    parser.add_argument("--dir", required=True, help="checkpoint directory")
    parser.add_argument("--slug", required=True, help="kebab-case slug for the filename")
    parser.add_argument("--title", default=None)
    parser.add_argument("--spec", default="<spec.md>", help="path to the spec used for this batch")
    parser.add_argument("--resume-command", default=None,
                        help="override the generated resume command (rarely needed)")
    parser.add_argument("--root", default=None, help="override the handoff root")
    args = parser.parse_args(argv)

    # Built here rather than passed in on purpose. Git Bash rewrites a leading
    # slash in an argument into a Windows path, which would silently turn
    # "/batch-run ..." into "C:/Program Files/Git/batch-run ..." in the doc.
    resume_command = args.resume_command or "/batch-run {} --spec {} --resume".format(
        args.units, args.spec)

    units = state.load_units(args.units)
    summary = state.report(args.dir, units)

    handoff_dir = find_root(args.root) / "docs" / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    path = handoff_dir / "{}-batch-{}.md".format(next_number(handoff_dir), args.slug)
    title = args.title or "Batch {}".format(args.slug)
    path.write_text(render(summary, title, resume_command), encoding="utf-8")
    print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
