"""batch-run ships in two repos: claude-config/skills/batch-run and
mrtoaster13-plugins/plugins/autonomous-workflows/skills/batch-run. A batch
started under one copy must resume under the other, so both must put a unit's
record in the same file. This file is identical in both repos.

tests/fixtures/foreign-checkpoint holds a checkpoint directory that the other
copy wrote. Regenerate it with the other copy's state.py if record names
change on purpose, and change both copies together.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import state  # noqa: E402

# Expected names are literals: the readable part, then the first 8 hex digits
# of the sha1 of the id, for any id that is not plain lowercase and filename
# safe, or that would land on batch-config.json.
EXPECTED_SLUGS = [
    ("unit-02", "unit-02"),
    ("Unit-01", "Unit-01.2f666dfc"),
    ("Unit-03", "Unit-03.b935b7ce"),
    ("a/b", "a_b.3ec69c85"),
    ("batch-config", "batch-config.1dd12472"),
    ("Batch-Config", "Batch-Config.3454032f"),
]

FOREIGN = HERE / "fixtures" / "foreign-checkpoint"
# What the other copy recorded in FOREIGN.
FOREIGN_VERIFIED = {"Unit-01", "unit-02", "a/b", "batch-config"}
FOREIGN_UNITS = [{"id": i} for i in ("Unit-01", "unit-02", "a/b", "batch-config", "Unit-03", "Batch-Config")]


@pytest.mark.parametrize("unit_id,slug", EXPECTED_SLUGS)
def test_both_copies_name_record_files_the_same_way(unit_id, slug):
    assert state.slug_for(unit_id) == slug
    assert state.record_path("cp", unit_id).name == slug + ".json"


def test_a_batch_written_by_the_other_copy_resumes_here(tmp_path):
    cdir = tmp_path / "checkpoint"
    shutil.copytree(str(FOREIGN), str(cdir))
    pending = [u["id"] for u in state.todo(str(cdir), FOREIGN_UNITS)]
    assert pending == ["Unit-03", "Batch-Config"]
    for unit_id in FOREIGN_VERIFIED:
        assert state.read_record(str(cdir), unit_id)["status"] == "verified"


def test_the_foreign_fixture_holds_one_file_per_recorded_unit():
    names = sorted(p.name for p in FOREIGN.iterdir())
    assert names == sorted([
        "Unit-01.2f666dfc.json", "unit-02.json", "a_b.3ec69c85.json",
        "batch-config.1dd12472.json", "Unit-03.b935b7ce.json"])
