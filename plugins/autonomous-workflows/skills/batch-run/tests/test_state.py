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


def test_a_legacy_record_does_not_mark_its_case_twin_done(tmp_path, monkeypatch):
    """A checkpoint written before the uppercase tag: Alpha.json says Alpha.

    On a case-insensitive filesystem, opening alpha.json reads that file. The
    unit_id inside it is Alpha, so alpha must still count as pending.
    """
    monkeypatch.setattr(state, "record_path", _fold_case(state.record_path))
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    legacy = {"unit_id": "Alpha", "status": "verified", "attempt": 1}
    (cdir / "alpha.json").write_text(json.dumps(legacy), encoding="utf-8")
    units = [{"id": "alpha"}, {"id": "Alpha"}]

    assert state.read_record(cdir, "alpha") is None
    assert [u["id"] for u in state.todo(cdir, units)] == ["alpha", "Alpha"]
    summary = state.report(cdir, units)
    assert summary["verified"] == 0


@pytest.mark.parametrize("payload", [
    {"status": "verified"},
    {"unit_id": "other", "status": "verified"},
    {"unit_id": None, "status": "verified"},
    {"unit_id": ["u1"], "status": "verified"},
])
def test_a_record_that_names_another_unit_is_pending(tmp_path, payload):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    state.record_path(cdir, "u1").write_text(json.dumps(payload), encoding="utf-8")

    assert state.read_record(cdir, "u1") is None
    assert [u["id"] for u in state.todo(cdir, [{"id": "u1"}])] == ["u1"]


def test_record_reads_back_its_own_unit_id(tmp_path):
    cdir = tmp_path / "checkpoint"
    state.record(cdir, "Alpha", "verified")
    assert state.read_record(cdir, "Alpha")["unit_id"] == "Alpha"


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

    # Only opens of the lock itself count. Stealing a stale lock also opens a
    # guard file next to it, which is not what this test is about.
    def busy(target, flags, *rest):
        if str(target) == str(path):
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


@pytest.mark.parametrize(
    "unit_id",
    [["a"], {"a": 1}, 7, None, True, "", "  "],
    ids=["list", "dict", "int", "null", "bool", "empty", "blank"],
)
def test_a_unit_id_that_is_not_a_non_empty_string_is_rejected_at_load(tmp_path, unit_id):
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"id": unit_id}]), encoding="utf-8")
    with pytest.raises(ValueError, match="unit id must be a non-empty string"):
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


def test_ids_that_differ_only_in_case_load_and_get_different_files(tmp_path):
    # An id with an uppercase letter gets a hash tag, so case twins load and
    # never share a file, even on a case-insensitive file system.
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"id": "Acme"}, {"id": "acme"}]), encoding="utf-8")
    assert [u["id"] for u in state.load_units(str(path))] == ["Acme", "acme"]
    assert state.record_path(tmp_path, "Acme").name.lower() != state.record_path(tmp_path, "acme").name.lower()
    state.record(tmp_path, "Acme", "verified")
    assert state.read_record(tmp_path, "acme") is None


def test_an_id_equal_to_another_ids_slug_is_rejected_at_load(tmp_path):
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"id": "a/b"}, {"id": state.slug_for("a/b")}]), encoding="utf-8")
    with pytest.raises(ValueError, match="would share the checkpoint file"):
        state.load_units(str(path))


def test_a_record_written_for_another_id_reads_as_pending(tmp_path):
    cdir = tmp_path / "checkpoint"
    state.record(cdir, "a", "verified")
    os.replace(str(state.record_path(cdir, "a")), str(state.record_path(cdir, "b")))
    assert state.read_record(cdir, "b") is None


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_a_units_file_with_a_non_finite_number_is_rejected_at_load(tmp_path, number):
    path = tmp_path / "units.json"
    path.write_text('[{"id": "a", "n": %s}]' % number, encoding="utf-8")
    with pytest.raises(ValueError, match="not JSON|out of range"):
        state.load_units(str(path))


# --- two callers that judge the same lock stale --------------------------

def test_a_caller_with_an_old_stale_judgment_does_not_take_a_lock_stolen_meanwhile(batch, monkeypatch):
    # B judges the lock stale, then A steals it and writes a
    # fresh lock before B acts. B must not delete A's lock and win too.
    _, _, cdir = batch
    assert state.claim(cdir, "unit-14", owner="dead") is True
    path = state.lock_path(cdir, "unit-14")
    old = time.time() - 3600
    os.utime(str(path), (old, old))

    real_getmtime = os.path.getmtime
    results = {}

    def a_steals_while_b_judges(target):
        seen = real_getmtime(target)
        if "a" not in results:
            monkeypatch.setattr(state.os.path, "getmtime", real_getmtime)
            results["a"] = state.claim(cdir, "unit-14", lease_seconds=60, owner="a")
        return seen

    monkeypatch.setattr(state.os.path, "getmtime", a_steals_while_b_judges)
    results["b"] = state.claim(cdir, "unit-14", lease_seconds=60, owner="b")

    assert results == {"a": True, "b": False}
    assert json.loads(path.read_text(encoding="utf-8"))["owner"] == "a"


@pytest.mark.parametrize("lease", [60, 0], ids=["lease-60", "lease-0"])
def test_a_fresh_steal_guard_makes_the_caller_lose(batch, lease):
    # Another caller is stealing right now.
    _, _, cdir = batch
    assert state.claim(cdir, "unit-15") is True
    path = state.lock_path(cdir, "unit-15")
    old = time.time() - 3600
    os.utime(str(path), (old, old))
    guard = path.with_name(path.name + ".steal")
    guard.write_text("", encoding="utf-8")

    assert state.claim(cdir, "unit-15", lease_seconds=lease) is False
    assert path.exists()
    assert guard.exists()


@pytest.mark.parametrize("offset", [-3600, 3600], ids=["old", "far-future"])
def test_a_steal_guard_left_by_a_dead_caller_is_cleared(batch, offset):
    # A stealer that died leaves its guard. One 60 seconds or
    # more off the clock is removed; that call still loses, the next one wins.
    _, _, cdir = batch
    assert state.claim(cdir, "unit-16") is True
    path = state.lock_path(cdir, "unit-16")
    old = time.time() - 3600
    os.utime(str(path), (old, old))
    guard = path.with_name(path.name + ".steal")
    guard.write_text("", encoding="utf-8")
    when = time.time() + offset
    os.utime(str(guard), (when, when))

    assert state.claim(cdir, "unit-16", lease_seconds=60) is False
    assert not guard.exists()
    assert state.claim(cdir, "unit-16", lease_seconds=60) is True
    assert not guard.exists()


def test_clearing_a_dead_guard_does_not_delete_a_live_one_made_meanwhile(batch, monkeypatch):
    # X judges the guard old, then before X removes
    # it, another caller clears it and W makes a fresh one. X must leave W's.
    _, _, cdir = batch
    assert state.claim(cdir, "unit-17") is True
    path = state.lock_path(cdir, "unit-17")
    old = time.time() - 3600
    os.utime(str(path), (old, old))
    guard = path.with_name(path.name + ".steal")
    guard.write_text("dead", encoding="utf-8")
    os.utime(str(guard), (old, old))

    real_getmtime = os.path.getmtime

    def swap_guard(target):
        seen = real_getmtime(target)
        if str(target) == str(guard):
            monkeypatch.setattr(state.os.path, "getmtime", real_getmtime)
            os.unlink(str(guard))
            guard.write_text("live", encoding="utf-8")
        return seen

    monkeypatch.setattr(state.os.path, "getmtime", swap_guard)
    assert state.claim(cdir, "unit-17", lease_seconds=60) is False
    assert guard.read_text(encoding="utf-8") == "live"


def test_a_stealer_does_not_delete_a_guard_that_replaced_its_own(batch, monkeypatch):
    # The stealer's own guard is cleared and another
    # caller makes a fresh one while the stealer works. The stealer must not
    # remove the fresh guard when it finishes.
    _, _, cdir = batch
    assert state.claim(cdir, "unit-18") is True
    path = state.lock_path(cdir, "unit-18")
    old = time.time() - 3600
    os.utime(str(path), (old, old))
    guard = path.with_name(path.name + ".steal")

    real_stat = os.stat
    swapped = []

    def swap_during_steal(target, *args, **kwargs):
        if str(target) == str(path) and guard.exists() and not swapped:
            swapped.append(True)
            os.unlink(str(guard))
            guard.write_text("other", encoding="utf-8")
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(state.os, "stat", swap_during_steal)
    state.claim(cdir, "unit-18", lease_seconds=60)
    assert swapped
    assert guard.exists() and guard.read_text(encoding="utf-8") == "other"


def test_an_id_equal_to_another_ids_tag_in_another_case_is_rejected_at_load(tmp_path):
    # Unit-01 lands on Unit-01.2f666dfc.json. unit-01.2f666dfc keeps its own
    # name, which a case-insensitive file system reads as the same file.
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"id": "Unit-01"}, {"id": state.slug_for("Unit-01").lower()}]),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="would share the checkpoint file"):
        state.load_units(str(path))


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-1e999"])
def test_a_record_with_a_non_finite_number_reads_as_pending(tmp_path, number):
    cdir = tmp_path / "checkpoint"
    cdir.mkdir()
    state.record_path(cdir, "u1").write_text(
        '{"unit_id": "u1", "status": "verified", "attempt": %s}' % number, encoding="utf-8")
    assert state.read_record(cdir, "u1") is None


def test_a_steal_guard_busy_for_a_moment_is_retried(batch, monkeypatch):
    # Windows can report the guard file in use while antivirus scans it.
    _, _, cdir = batch
    assert state.claim(cdir, "unit-19") is True
    path = state.lock_path(cdir, "unit-19")
    old = time.time() - 3600
    os.utime(str(path), (old, old))
    guard = str(path.with_name(path.name + ".steal"))
    real_open, busy = os.open, []

    def busy_guard_once(target, flags, *rest):
        if str(target) == guard and not busy:
            busy.append(target)
            raise PermissionError(13, "in use", target)
        return real_open(target, flags, *rest)

    monkeypatch.setattr(state.os, "open", busy_guard_once)
    assert state.claim(cdir, "unit-19", lease_seconds=60) is True
    assert busy


# --- a units file strict JSON rejects is a clean CLI error ------------------

BAD_UNITS = ['[{"id": "a", "n": NaN}]', '[{"id": "a", "n": Infinity}]', '[{"id": "a", "n": 1e999}]']


@pytest.mark.parametrize("command", ["todo", "report"])
@pytest.mark.parametrize("text", BAD_UNITS + [None])
def test_the_todo_and_report_cli_exit_1_on_a_units_file_strict_json_rejects(
        tmp_path, capsys, command, text):
    units_file = tmp_path / "units.json"
    if text is not None:
        units_file.write_text(text, encoding="utf-8")
    rc = state._main([command, "--dir", str(tmp_path / "checkpoint"), "--units", str(units_file)])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.startswith("error: ")
    assert "Traceback" not in out.err
    assert out.out == ""


@pytest.mark.parametrize("text", BAD_UNITS + [None])
def test_the_handoff_cli_exits_1_on_a_units_file_strict_json_rejects(tmp_path, capsys, text):
    sys.path.insert(0, str(SCRIPTS))
    import handoff

    units_file = tmp_path / "units.json"
    if text is not None:
        units_file.write_text(text, encoding="utf-8")
    root = tmp_path / "repo"
    rc = handoff._main(["--units", str(units_file), "--dir", str(tmp_path / "checkpoint"),
                        "--slug", "bad", "--root", str(root)])
    out = capsys.readouterr()
    assert rc == 1
    assert out.err.startswith("error: ")
    assert "Traceback" not in out.err
    assert not (root / "docs" / "handoffs").exists() or not list(
        (root / "docs" / "handoffs").iterdir())


# --- release retries a lock Windows reports in use --------------------------

def test_release_retries_while_windows_reports_the_lock_in_use(batch, monkeypatch):
    _, _, cdir = batch
    assert state.claim(cdir, "unit-20") is True
    path = state.lock_path(cdir, "unit-20")
    real_unlink, busy = os.unlink, []

    def busy_once(target, *rest, **kw):
        if str(target) == str(path) and not busy:
            busy.append(target)
            raise PermissionError(13, "in use", target)
        return real_unlink(target, *rest, **kw)

    monkeypatch.setattr(state.os, "unlink", busy_once)
    assert state.release(cdir, "unit-20") is True
    assert busy
    assert not path.exists()


# --- nesting depth checked after the parser ---------------------------------

def _nested_list(depth):
    value = []
    for _ in range(depth):
        value = [value]
    return value


def test_a_units_file_nested_too_deeply_is_a_value_error_when_json_parses_it(tmp_path, monkeypatch):
    # ELI-668: on CI's Python 3.14.9, json.load parsed 100000 nested brackets
    # without RecursionError, so load_units reported a missing id instead.
    # Patching json.load reproduces that parser on any Python.
    path = tmp_path / "units.json"
    path.write_text("[]", encoding="utf-8")
    deep = _nested_list(5000)
    monkeypatch.setattr(state.json, "load", lambda *a, **k: deep)
    with pytest.raises(ValueError, match="units file is nested too deeply to parse"):
        state.load_units(str(path))


def test_units_with_moderately_nested_fields_still_load(tmp_path):
    path = tmp_path / "units.json"
    path.write_text(json.dumps([{"id": "a", "payload": _nested_list(50)}]), encoding="utf-8")
    assert [u["id"] for u in state.load_units(str(path))] == ["a"]
