"""Tests for the batch-run checkpoint store.

These simulate the failure that motivated the whole design: a run that dies
partway through, with no chance to run cleanup code. No agents are involved,
so the suite finishes in about a second.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import state  # noqa: E402


def make_units(count):
    return [{"id": "unit-{:02d}".format(i), "payload": "p{}".format(i)} for i in range(count)]


@pytest.fixture
def batch(tmp_path):
    units = make_units(30)
    units_file = tmp_path / "units.json"
    units_file.write_text(json.dumps(units), encoding="utf-8")
    return units, units_file, tmp_path / "checkpoint"


# --- 1. interruption partway through a batch -------------------------------

def test_resume_reprocesses_exactly_the_unverified(batch):
    units, _, cdir = batch
    killed_after = 17
    for unit in units[:killed_after]:
        state.record(cdir, unit["id"], "verified", artifact="out/{}.txt".format(unit["id"]))

    pending = state.todo(cdir, units)

    assert len(pending) == len(units) - killed_after
    assert {u["id"] for u in pending} == {u["id"] for u in units[killed_after:]}
    # And the finished work is never redone.
    assert not any(u["id"] in {v["id"] for v in units[:killed_after]} for u in pending)


def test_written_but_unverified_is_not_done(batch):
    """The exact hole the design exists to close.

    A worker finished and the session died before its checker ran. The artifact
    exists on disk and looks finished. It is not finished, and resume must pick
    it back up.
    """
    units, _, cdir = batch
    state.record(cdir, "unit-00", "written", artifact="out/unit-00.txt")
    state.record(cdir, "unit-01", "failed", reason="invented a certification")
    state.record(cdir, "unit-02", "verified", artifact="out/unit-02.txt")

    pending_ids = {u["id"] for u in state.todo(cdir, units)}

    assert "unit-00" in pending_ids
    assert "unit-01" in pending_ids
    assert "unit-02" not in pending_ids


def test_report_counts_and_completeness(batch):
    units, _, cdir = batch
    for unit in units:
        state.record(cdir, unit["id"], "verified")
    summary = state.report(cdir, units)

    assert summary["complete"] is True
    assert summary["verified"] == 30

    state.record(cdir, "unit-09", "failed", reason="fabricated a date")
    summary = state.report(cdir, units)

    assert summary["complete"] is False
    assert summary["verified"] == 29
    assert summary["failures"] == [{"id": "unit-09", "reason": "fabricated a date"}]


def test_no_unit_can_be_silently_dropped(batch):
    """Every unit lands in exactly one bucket, always."""
    units, _, cdir = batch
    for i, unit in enumerate(units):
        status = ("verified", "written", "failed", "pending")[i % 4]
        if status != "pending":
            state.record(cdir, unit["id"], status)
    summary = state.report(cdir, units)
    bucketed = sum(len(v) for v in summary["ids"].values())

    assert bucketed == len(units)
    assert summary["verified"] + len(state.todo(cdir, units)) == len(units)


# --- 2. a record file mangled by a process killed mid write ----------------

@pytest.mark.parametrize("corruption", [
    '{"unit_id": "unit-03", "status": "veri',   # truncated mid write
    "",                                          # zero bytes
    '{"unit_id": "unit-03", "status": "done"}',  # status outside the enum
    '["not", "an", "object"]',                   # right JSON, wrong shape
])
def test_unusable_record_reads_as_pending(batch, corruption):
    units, _, cdir = batch
    state.record(cdir, "unit-03", "verified")
    state.record_path(cdir, "unit-03").write_text(corruption, encoding="utf-8")

    assert state.read_record(cdir, "unit-03") is None
    assert "unit-03" in {u["id"] for u in state.todo(cdir, units)}
    assert state.report(cdir, units)["complete"] is False


def test_atomic_write_leaves_no_partial_file(batch):
    """os.replace means a reader either sees the old record or the new one."""
    units, _, cdir = batch
    state.record(cdir, "unit-04", "written")
    path = state.record_path(cdir, "unit-04")

    for _ in range(50):
        state.record(cdir, "unit-04", "verified", artifact="out/unit-04.txt")
        json.loads(path.read_text(encoding="utf-8"))  # never a half file

    leftovers = [p.name for p in Path(cdir).iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# --- 3. concurrent workers ------------------------------------------------

def test_parallel_records_do_not_lose_each_other(batch):
    """The failure mode of a single shared checkpoint file.

    Thirty workers finish at once. With one shared JSON these updates would
    clobber each other; with one file per unit there is nothing to contend on.
    """
    units, _, cdir = batch
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda u: state.record(cdir, u["id"], "verified"), units))

    summary = state.report(cdir, units)
    assert summary["verified"] == 30
    assert summary["complete"] is True


def test_only_one_claimant_wins_a_unit(batch):
    _, _, cdir = batch
    with ThreadPoolExecutor(max_workers=8) as pool:
        won = list(pool.map(lambda i: state.claim(cdir, "unit-05", owner="w{}".format(i)), range(8)))

    assert sum(1 for w in won if w) == 1
    assert state.claim(cdir, "unit-05") is False
    state.release(cdir, "unit-05")
    assert state.claim(cdir, "unit-05") is True


def test_a_dead_workers_lease_expires(batch):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-06", lease_seconds=1800, owner="dead") is True
    # The holder died. A lease long past its window is stealable, not a deadlock.
    assert state.claim(cdir, "unit-06", lease_seconds=0, owner="live") is True


# --- ids from user data ---------------------------------------------------

@pytest.mark.parametrize("unit_id", [
    "acme/secops analyst",
    "../../etc/passwd",
    "..",
    "x" * 200,
    "café-rôle",
])
def test_hostile_ids_stay_inside_the_checkpoint_dir(batch, unit_id):
    _, _, cdir = batch
    state.record(cdir, unit_id, "verified")
    path = state.record_path(cdir, unit_id)

    assert path.parent.resolve() == Path(cdir).resolve()
    assert state.read_record(cdir, unit_id)["status"] == "verified"


def test_similar_ids_do_not_collide(batch):
    _, _, cdir = batch
    state.record(cdir, "acme/analyst", "verified")
    state.record(cdir, "acme analyst", "failed", reason="different unit")

    assert state.read_record(cdir, "acme/analyst")["status"] == "verified"
    assert state.read_record(cdir, "acme analyst")["status"] == "failed"


def test_duplicate_unit_ids_are_rejected_at_load(tmp_path):
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"id": "a"}, {"id": "a"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        state.load_units(str(path))


def test_units_without_an_id_are_rejected_at_load(tmp_path):
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"jd": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="'id'"):
        state.load_units(str(path))


# --- the CLI the agents actually call -------------------------------------

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def run_cli(*argv):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "state.py")] + list(argv),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_cli_record_then_report_strict(batch):
    units, units_file, cdir = batch
    for unit in units:
        assert run_cli("record", "--dir", str(cdir), "--id", unit["id"],
                       "--status", "verified").returncode == 0

    done = run_cli("report", "--units", str(units_file), "--dir", str(cdir), "--strict")
    assert done.returncode == 0
    assert json.loads(done.stdout.decode())["verified"] == 30

    run_cli("record", "--dir", str(cdir), "--id", "unit-11", "--status", "failed",
            "--reason", "hallucinated an employer")
    incomplete = run_cli("report", "--units", str(units_file), "--dir", str(cdir), "--strict")
    assert incomplete.returncode == 1


def test_cli_todo_is_json_the_orchestrator_can_read(batch):
    units, units_file, cdir = batch
    run_cli("record", "--dir", str(cdir), "--id", "unit-00", "--status", "verified")
    out = run_cli("todo", "--units", str(units_file), "--dir", str(cdir), "--limit", "5")

    pending = json.loads(out.stdout.decode())
    assert len(pending) == 5
    assert pending[0]["id"] == "unit-01"
    assert pending[0]["payload"] == "p1"  # the payload survives the round trip


def test_cli_rejects_a_status_outside_the_enum(batch):
    _, _, cdir = batch
    assert run_cli("record", "--dir", str(cdir), "--id", "unit-00",
                   "--status", "probably-fine").returncode != 0


# --- the handoff rendering ------------------------------------------------

def test_handoff_lists_every_unfinished_unit(batch, tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import handoff

    units, units_file, cdir = batch
    for unit in units[:27]:
        state.record(cdir, unit["id"], "verified")
    state.record(cdir, "unit-27", "written")
    state.record(cdir, "unit-28", "failed", reason="invented a CISSP")

    root = tmp_path / "repo"
    (root / "docs" / "handoffs").mkdir(parents=True)
    (root / "docs" / "handoffs" / "009-earlier.md").write_text("x", encoding="utf-8")

    rc = handoff._main(["--units", str(units_file), "--dir", str(cdir),
                        "--slug", "resumes", "--spec", "spec.md", "--root", str(root)])
    assert rc == 0

    written = list((root / "docs" / "handoffs").glob("*-batch-resumes.md"))
    assert len(written) == 1
    assert written[0].name.startswith("010-")  # numeric max plus one, not lexicographic

    body = written[0].read_text(encoding="utf-8")
    assert "27 of 30 units verified" in body
    assert "unit-27" in body and "unit-28" in body and "invented a CISSP" in body
    assert "unit-29" in body  # never started, still accounted for
    assert "Status: in-progress" in body
    # The resume command is generated, not passed through a shell that would
    # rewrite its leading slash into a Windows path.
    assert "/batch-run" in body
    assert "Program Files" not in body


def test_verifying_keeps_the_artifact_path_the_writer_recorded(tmp_path):
    """The verifier records status and reason only; the path must survive it.

    Without carry-forward the artifact is dropped at the exact moment a unit
    succeeds, so a finished batch has nothing on disk saying where its outputs
    went.
    """
    state.record(str(tmp_path), "alpha", "written", artifact=r"C:\out\alpha.md", worker="sonnet")
    state.record(str(tmp_path), "alpha", "verified", reason="holds up")

    rec = state.read_record(str(tmp_path), "alpha")
    assert rec["status"] == "verified"
    assert rec["artifact"] == r"C:\out\alpha.md"
    assert rec["worker"] == "sonnet"
    assert rec["reason"] == "holds up"


def test_an_explicit_artifact_still_wins_over_the_inherited_one(tmp_path):
    state.record(str(tmp_path), "alpha", "written", artifact=r"C:\out\old.md")
    state.record(str(tmp_path), "alpha", "written", artifact=r"C:\out\new.md")
    assert state.read_record(str(tmp_path), "alpha")["artifact"] == r"C:\out\new.md"


def test_reason_does_not_leak_across_a_retry(tmp_path):
    """reason describes the current status, so a retry must not inherit it."""
    state.record(str(tmp_path), "alpha", "failed", reason="invented a CISSP")
    state.record(str(tmp_path), "alpha", "written", artifact=r"C:\out\alpha.md")

    rec = state.read_record(str(tmp_path), "alpha")
    assert rec["status"] == "written"
    assert "reason" not in rec
