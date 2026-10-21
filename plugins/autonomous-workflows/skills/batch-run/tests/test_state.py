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
import time
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


# NTFS and the default macOS filesystem compare names without case, so these
# tests compare names with .lower() and never depend on the host filesystem.

@pytest.mark.parametrize("ids", [
    ["Alpha", "alpha"],
    ["ABC", "abc", "aBc"],
    ["Unit-01", "unit-01", "UNIT-01"],
    ["Acme/Analyst", "acme/analyst"],
    ["Batch-Config", "batch-config", "BATCH-CONFIG"],
])
def test_ids_that_differ_only_in_case_get_different_files(tmp_path, ids):
    for path_for in (state.record_path, state.lock_path):
        names = [path_for(tmp_path, unit_id).name.lower() for unit_id in ids]
        assert len(set(names)) == len(ids), names


def _fold_case(path_for):
    """Wrap a path function so it behaves as on a case-insensitive filesystem."""
    def folded(checkpoint_dir, unit_id):
        path = path_for(checkpoint_dir, unit_id)
        return path.with_name(path.name.lower())
    return folded


def test_a_verified_id_does_not_mark_its_case_twin_done(tmp_path, monkeypatch):
    """The probe from handoff 002: Alpha verified, then units [alpha, Alpha]."""
    monkeypatch.setattr(state, "record_path", _fold_case(state.record_path))
    cdir = tmp_path / "checkpoint"
    state.record(cdir, "Alpha", "verified")
    units = [{"id": "alpha"}, {"id": "Alpha"}]

    assert [u["id"] for u in state.todo(cdir, units)] == ["alpha"]
    summary = state.report(cdir, units)
    assert summary["verified"] == 1
    assert summary["ids"]["pending"] == ["alpha"]


def test_a_claimed_id_does_not_lock_its_case_twin(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lock_path", _fold_case(state.lock_path))
    cdir = tmp_path / "checkpoint"
    assert state.claim(cdir, "Alpha", owner="one") is True
    assert state.claim(cdir, "alpha", owner="two") is True


@pytest.mark.parametrize("unit_id", [
    "alpha", "abc", "unit-01", "acme-secops-analyst", "v1.2_final", "x" * 80,
], ids=["alpha", "abc", "unit-01", "kebab", "dots-underscore", "max-length"])
def test_lowercase_safe_ids_keep_their_plain_slug(unit_id):
    assert state.slug_for(unit_id) == unit_id


@pytest.mark.parametrize("unit_id, slug", [
    ("acme/analyst", "acme_analyst.27ae6057"),
    ("Acme/Analyst", "Acme_Analyst.2b3deb13"),
    ("Batch-Config", "Batch-Config.3454032f"),
    ("batch-config", "batch-config.1dd12472"),
], ids=["unsafe", "unsafe-upper", "config-upper", "config"])
def test_ids_that_were_already_tagged_keep_their_slug(unit_id, slug):
    assert state.slug_for(unit_id) == slug


def test_an_uppercase_id_gets_a_lowercase_hex_tag():
    slug = state.slug_for("Alpha")
    readable, _, tag = slug.rpartition(".")
    assert readable == "Alpha"
    assert len(tag) == 8 and tag == tag.lower()
    assert all(c in "0123456789abcdef" for c in tag)


def test_duplicate_unit_ids_are_rejected_at_load(tmp_path):
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"id": "a"}, {"id": "a"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        state.load_units(str(path))


def test_a_units_file_with_a_byte_order_mark_loads(tmp_path):
    path = tmp_path / "units.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"id": "a"}]).encode("utf-8"))
    assert state.load_units(str(path)) == [{"id": "a"}]


def test_a_units_file_nested_too_deep_is_a_clean_error(tmp_path):
    path = tmp_path / "units.json"
    path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(ValueError, match="nested too deeply") as caught:
        state.load_units(str(path))
    # One error, not a RecursionError traceback chained in front of it.
    assert caught.value.__suppress_context__


def test_a_record_with_a_byte_order_mark_still_counts_as_verified(batch):
    units, _, cdir = batch
    state.record(cdir, "unit-05", "verified")
    path = state.record_path(cdir, "unit-05")
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert state.read_record(cdir, "unit-05")["status"] == "verified"
    assert "unit-05" not in {u["id"] for u in state.todo(cdir, units)}


def test_a_record_nested_too_deep_reads_as_pending(batch):
    _, _, cdir = batch
    state.record(cdir, "unit-06", "verified")
    state.record_path(cdir, "unit-06").write_text("[" * 100_000 + "]" * 100_000,
                                                 encoding="utf-8")
    assert state.read_record(cdir, "unit-06") is None


@pytest.mark.parametrize("body", [
    "", "{", "[" * 100_000 + "]" * 100_000, '{"at": "x"}', '{"at": 1e999}',
    '{"at": 99999999999}',
], ids=["empty", "truncated", "deep", "text-at", "infinite-at", "future-at-field"])
def test_a_lock_is_aged_by_its_file_time_not_its_contents(batch, body):
    _, _, cdir = batch
    path = state.lock_path(cdir, "unit-07")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    # Fresh: a winner may have created it and not written it yet.
    assert state.claim(cdir, "unit-07", lease_seconds=60) is False
    old = time.time() - 120
    os.utime(str(path), (old, old))
    assert state.claim(cdir, "unit-07", lease_seconds=60) is True


def test_a_zero_lease_is_stealable_even_with_a_file_time_ahead_of_the_clock(batch):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-08") is True
    path = state.lock_path(cdir, "unit-08")
    ahead = time.time() + 30
    os.utime(str(path), (ahead, ahead))
    assert state.claim(cdir, "unit-08", lease_seconds=0) is True


@pytest.mark.parametrize("lease", [60, 0], ids=["lease-60", "lease-0"])
def test_a_lock_whose_time_cannot_be_read_is_left_alone(batch, monkeypatch, lease):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-09") is True
    path = state.lock_path(cdir, "unit-09")
    old = time.time() - 3600
    os.utime(str(path), (old, old))

    def vanished(_path):
        raise FileNotFoundError(_path)

    monkeypatch.setattr(state.os.path, "getmtime", vanished)
    # Stale on disk, but the caller cannot see that, so it must not unlink.
    # A zero lease steals any lock it can age, but not one it cannot.
    assert state.claim(cdir, "unit-09", lease_seconds=lease) is False
    assert path.exists()


@pytest.mark.parametrize("seconds_ahead, won", [
    (30, False),        # clock skew within the lease: still held
    (10 ** 9, True),    # decades ahead: a bogus time, so stale
], ids=["skew", "far-future"])
def test_a_lock_dated_ahead_of_the_clock_is_held_only_within_a_lease(
        batch, seconds_ahead, won):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-11") is True
    path = state.lock_path(cdir, "unit-11")
    ahead = time.time() + seconds_ahead
    os.utime(str(path), (ahead, ahead))
    assert state.claim(cdir, "unit-11", lease_seconds=60) is won


@pytest.mark.parametrize("busy_on", [1, 2], ids=["first-open", "retry-open"])
def test_a_lock_windows_reports_in_use_for_a_moment_is_retried(batch, monkeypatch, busy_on):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-10") is True
    path = state.lock_path(cdir, "unit-10")
    old = time.time() - 3600
    os.utime(str(path), (old, old))
    real_open, calls = os.open, []

    def busy(target, flags, *rest):
        calls.append(target)
        if len(calls) == busy_on:
            raise PermissionError(13, "in use", target)
        return real_open(target, flags, *rest)

    monkeypatch.setattr(state.os, "open", busy)
    assert state.claim(cdir, "unit-10", lease_seconds=60) is True
    assert len(calls) == 3


def test_a_stale_lock_the_caller_cannot_delete_reads_as_held(batch, monkeypatch):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-13") is True
    path = state.lock_path(cdir, "unit-13")
    old = time.time() - 3600
    os.utime(str(path), (old, old))

    def denied(target):
        raise PermissionError(13, "Permission denied", target)

    monkeypatch.setattr(state.os, "unlink", denied)
    # The documented exception: no raise, just a loss, on every call.
    assert state.claim(cdir, "unit-13", lease_seconds=60) is False
    assert state.claim(cdir, "unit-13", lease_seconds=60) is False
    assert path.exists()


def _always_denied(target, flags, *rest):
    raise PermissionError(13, "Permission denied", target)


def test_a_lock_that_stays_denied_raises_instead_of_reading_as_held(batch, monkeypatch):
    _, _, cdir = batch
    monkeypatch.setattr(state, "SHARING_RETRY_DELAYS", (0,))
    monkeypatch.setattr(state.os, "open", _always_denied)
    with pytest.raises(PermissionError):
        state.claim(cdir, "unit-12")


def test_the_claim_cli_exits_3_when_the_lock_cannot_be_created(batch, monkeypatch, capsys):
    _, _, cdir = batch
    monkeypatch.setattr(state, "SHARING_RETRY_DELAYS", (0,))
    monkeypatch.setattr(state.os, "open", _always_denied)
    assert state._main(["claim", "--dir", str(cdir), "--id", "unit-12"]) == 3
    out = capsys.readouterr()
    assert "held-by-another" not in out.out
    assert "cannot create the lock" in out.err


def test_a_write_retries_while_windows_reports_the_file_in_use(batch, monkeypatch):
    _, _, cdir = batch
    real_replace, calls = os.replace, []

    def busy_twice(src, dst):
        calls.append(dst)
        if len(calls) <= 2:
            raise PermissionError("in use")
        real_replace(src, dst)

    monkeypatch.setattr(state.os, "replace", busy_twice)
    state.record(cdir, "unit-08", "verified")
    assert len(calls) == 3
    assert state.read_record(cdir, "unit-08")["status"] == "verified"


def test_a_read_retries_while_windows_reports_the_file_in_use(batch, monkeypatch):
    _, _, cdir = batch
    state.record(cdir, "unit-09", "verified")
    real_open, calls = open, []

    def busy_once(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("in use")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(state, "open", busy_once, raising=False)
    assert state.read_record(cdir, "unit-09")["status"] == "verified"
    assert len(calls) == 2


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


@pytest.mark.parametrize("argv", [
    ["todo", "--units", "u.json", "--limit", "-1"],
    ["claim", "--id", "a", "--lease-seconds", "-5"],
])
def test_cli_rejects_negative_limits_and_leases(batch, argv):
    _, _, cdir = batch
    result = run_cli(*(argv + ["--dir", str(cdir)]))
    assert result.returncode == 2
    assert b"must be 0 or more" in result.stderr


def test_the_release_cli_drops_a_held_lock(batch):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-14") is True
    result = run_cli("release", "--dir", str(cdir), "--id", "unit-14")
    assert result.returncode == 0
    assert result.stdout.decode().strip() == "released"
    assert not state.lock_path(cdir, "unit-14").exists()


def test_the_release_cli_exits_4_when_there_is_no_lock(batch):
    _, _, cdir = batch
    cdir.mkdir(parents=True, exist_ok=True)
    assert state.release(cdir, "unit-15") is False
    result = run_cli("release", "--dir", str(cdir), "--id", "unit-15")
    assert result.returncode == 4
    assert b"released" not in result.stdout
    assert b"cannot release" in result.stderr


def test_the_release_cli_exits_4_when_the_lock_cannot_be_removed(batch):
    _, _, cdir = batch
    # A directory where the lock file should be: unlink fails on every OS.
    path = state.lock_path(cdir, "unit-16")
    path.mkdir(parents=True)
    assert state.release(cdir, "unit-16") is False
    result = run_cli("release", "--dir", str(cdir), "--id", "unit-16")
    assert result.returncode == 4
    assert b"released" not in result.stdout
    assert b"cannot release" in result.stderr
    assert path.is_dir()


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
    state.record(str(tmp_path), "alpha", "written", artifact=r"C:\out\alpha.md", worker="haiku")
    state.record(str(tmp_path), "alpha", "verified", reason="holds up")

    rec = state.read_record(str(tmp_path), "alpha")
    assert rec["status"] == "verified"
    assert rec["artifact"] == r"C:\out\alpha.md"
    assert rec["worker"] == "haiku"
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


@pytest.mark.parametrize("unit_id", ["batch-config", "Batch-Config", "BATCH-CONFIG"])
def test_no_unit_record_lands_on_the_config_file(tmp_path, unit_id):
    cdir = tmp_path / "checkpoint"
    assert state.record_path(cdir, unit_id).name.lower() != state.CONFIG_NAME
    state.write_config(cdir, {"choice": "kept"})
    state.record(cdir, unit_id, "verified")
    assert state.read_config(cdir) == {"choice": "kept"}
    assert state.read_record(cdir, unit_id)["status"] == "verified"


@pytest.mark.parametrize("unit_id", ["batch-configs", "batch-config.json", "my-batch-config"])
def test_ids_near_the_config_name_keep_their_plain_slug(unit_id):
    assert state.slug_for(unit_id) == unit_id


def test_the_config_reads_back_what_was_written(tmp_path):
    cdir = tmp_path / "checkpoint"
    assert state.read_config(cdir) is None
    state.write_config(cdir, {"worker": {"model": "haiku"}})
    assert state.read_config(cdir) == {"worker": {"model": "haiku"}}
    assert sorted(p.name for p in cdir.iterdir()) == [state.CONFIG_NAME]


@pytest.mark.parametrize("body", [b'{"worker"', b"\xff\xfe", b"[" * 100_000],
                         ids=["truncated", "not-utf8", "too-deep"])
def test_an_unreadable_config_reads_as_none(tmp_path, body):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    state.config_path(cdir).write_bytes(body)
    assert state.read_config(cdir) is None


def test_a_config_with_a_byte_order_mark_reads(tmp_path):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    state.config_path(cdir).write_bytes(b"\xef\xbb\xbf" + b'{"choice": "x"}')
    assert state.read_config(cdir) == {"choice": "x"}
