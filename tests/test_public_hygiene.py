"""The repo is public. AGENTS.md says nothing here may name private paths
or hosts. This test scans every tracked text file for the ones that have
leaked before (ELI-891)."""

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
THIS_FILE = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()

NAME = r"[^\\/\s`'\"<>]+"
PATTERNS = [
    ("Windows user profile path", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+" + NAME, re.I)),
    ("user profile path", re.compile(r"(?<![\w./])(?:/mnt/[a-z]|/[a-z])?/(?:Users|home)/" + NAME)),
    ("user AppData path", re.compile(r"(?:~|%USERPROFILE%)[\\/]+AppData", re.I)),
    ("fleet checkout path", re.compile(r"(?:~|\$HOME)[\\/]fleet\b")),
    ("private host name", re.compile(r"\b(?:mini[-_ ]?pc|mac[-_ ]?mini|bc[-_ ]?250)\b", re.I)),
    ("private host name", re.compile(r"\bMini's\b|\bby Mini\b")),
]

# This file is not scanned: its examples name private paths on purpose.


def decode_text(data):
    """Decode a tracked file, or return None for a binary file."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def find_private(text):
    """Return (label, matched text) for each private path or host in text."""
    hits = []
    for label, pattern in PATTERNS:
        hits.extend((label, m.group(0)) for m in pattern.finditer(text))
    return hits


def tracked_text_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(ROOT), check=True,
        stdout=subprocess.PIPE,
    ).stdout
    for name in out.decode("utf-8").split("\0"):
        if not name or name == THIS_FILE:
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        text = decode_text(path.read_bytes())
        if text is not None:
            yield name, text


@pytest.mark.parametrize("text", [
    r"see C:\Users\someone\AppData\Local\Temp\x.html",
    "copied to c:/Users/someone/repo",
    "The Mac checkouts `~/fleet/repos/claude-config`",
    "ran on minipc", "the BC-250 box", "checked bc250", "macmini says",
    "Use Mini's branch", "by Mini, 2026-10-20",
    # Regression cases from the adversarial review.
    "/c/Users/someone/x", "/mnt/c/Users/someone", "/Users/someone/code",
    "/home/someone/fleet", r"%USERPROFILE%\AppData\Local", r"~\AppData\Local\Temp",
    "the `~/fleet` checkout", "$HOME/fleet/repos", r"~\fleet\repos",
    "mini-pc", "BC 250", "bc_250", "the Mac mini",
])
def test_private_paths_and_hosts_are_found(text):
    assert find_private(text)


@pytest.mark.parametrize("text", [
    "Drop anything under `AppData`, `Temp`, `.cache`",
    "the plugin uses `$HOME`",
    "a minimal test", "Gemini and minimize", "MiniMax model",
    "GPT-4o Mini is cheap", "Mini batch sizes", "/home/ is a folder", "see docs/Users/guide",
])
def test_ordinary_text_is_not_flagged(text):
    assert find_private(text) == []


def test_utf16_text_files_are_scanned(tmp_path):
    path = tmp_path / "note.txt"
    path.write_bytes("C:\\Users\\someone\\x".encode("utf-16"))
    assert find_private(decode_text(path.read_bytes()))


def test_no_tracked_file_names_a_private_path_or_host():
    leaks = []
    for name, text in tracked_text_files():
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, match in find_private(line):
                leaks.append("{}:{}: {}: {}".format(name, lineno, label, match))
    assert leaks == [], "\n".join(leaks)
