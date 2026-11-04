"""Durable per-unit checkpoint store for batch-run.

One small file per work unit under a checkpoint directory, written atomically
(temp file plus `os.replace`). Parallel workers never touch the same file, so
the record path needs no locking. On Windows `os.replace` and `open` fail with
PermissionError while another process holds the file open, so a write or read
that hits one retries a few times before giving up.

The invariant this module exists to enforce:

    A unit is done if and only if its record file exists, parses, names the
    unit's own id in "unit_id", and carries status "verified". Missing,
    malformed, a different "unit_id", "written", and "failed" all mean pending.

That is why a killed session needs no error handler. Failure is the absence of
a success record, not an event someone has to catch on the way down.

The directory also holds batch-config.json, the models the run committed to.
This module owns that file's name, reads and writes, and keeps every unit
record off it. estimate.py owns what goes in it.

CLI:
    python state.py todo    --units units.json --dir checkpoint [--limit N] [--max-attempts 2]
    python state.py failures --units units.json --dir checkpoint
    python state.py record  --dir checkpoint --id ID --status verified [...]
    python state.py report  --units units.json --dir checkpoint [--strict]
    python state.py claim   --dir checkpoint --id ID [--lease-seconds 1800] [--owner OWNER]
    python state.py release --dir checkpoint --id ID [--owner OWNER]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("pending", "written", "verified", "failed")
DONE = "verified"
DEFAULT_LEASE_SECONDS = 1800
# How many verifier rejections a unit may collect, across every run, before
# todo leaves it out. Matches maxAttempts in workflows/batch.js.
DEFAULT_MAX_ATTEMPTS = 2
# Seconds to wait between tries when Windows reports the file is in use.
SHARING_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SLUG = 80
CONFIG_NAME = "batch-config.json"
_CONFIG_STEM = CONFIG_NAME[:-len(".json")]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug_for(unit_id):
    """Map an arbitrary unit id onto a safe, collision-free filename stem.

    Ids come from user data and may contain slashes, spaces, or path tricks.
    Anything that is not already filename-safe gets sanitized and tagged with a
    hash of the original, so two ids that sanitize alike still get different
    files. The hash is lowercase hex.

    NTFS and the default macOS filesystem compare names without case, so an
    id with an uppercase letter is tagged the same way. Only plain lowercase
    ids keep their own name, and two ids that differ only in case, such as
    Alpha and alpha, never share a record or lock file. The readable part
    keeps its case. An id that would land on the config file, in any case, is
    tagged too, so a unit record and the saved models never overwrite each
    other. A unit whose id had an uppercase letter but no tag before the
    uppercase rule has a new slug, so it is pending again and gets redone, and an old lock file
    for it no longer holds the unit. On a filesystem that ignores case, that
    old lock still blocks the unit's lowercase twin until its lease expires,
    and read_record ignores the old record because its unit_id differs.
    """
    if not isinstance(unit_id, str) or not unit_id.strip():
        raise ValueError("unit id must be a non-empty string")
    safe = _UNSAFE.sub("_", unit_id)
    if (safe != unit_id or safe in (".", "..") or len(safe) > _MAX_SLUG
            or safe != safe.lower() or safe.lower() == _CONFIG_STEM):
        digest = hashlib.sha1(unit_id.encode("utf-8")).hexdigest()[:8]
        safe = "{}.{}".format(safe[:_MAX_SLUG], digest)
    return safe


def record_path(checkpoint_dir, unit_id):
    return Path(checkpoint_dir) / "{}.json".format(slug_for(unit_id))


def lock_path(checkpoint_dir, unit_id):
    return Path(checkpoint_dir) / "{}.lock".format(slug_for(unit_id))


def config_path(checkpoint_dir):
    return Path(checkpoint_dir) / CONFIG_NAME


def _retry_while_in_use(action):
    """Run action, retrying a PermissionError a few times, then re-raising it."""
    for delay in SHARING_RETRY_DELAYS:
        try:
            return action()
        except PermissionError:
            time.sleep(delay)
    return action()


def _read_json(path):
    """Parse a JSON file. utf-8-sig also reads a file saved with a byte order mark."""
    def read():
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    return _retry_while_in_use(read)


def _at_least(minimum, maximum=None):
    """An argparse type: an integer no smaller than minimum and, when a
    maximum is given, no larger than it."""
    def integer(text):
        value = int(text)
        if value < minimum:
            raise argparse.ArgumentTypeError("must be {} or more, got {}".format(minimum, value))
        if maximum is not None and value > maximum:
            raise argparse.ArgumentTypeError("must be {} or less, got {}".format(maximum, value))
        return value
    return integer


def _atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    # The process and thread ids keep two writers of one file off the same
    # temp path. A failed write removes its temp file before re-raising.
    tmp = path.parent / "{}.{}.{}.tmp".format(path.name, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        _retry_while_in_use(lambda: os.replace(tmp, path))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def read_config(checkpoint_dir):
    """Return the saved models record as parsed, or None if it is absent or
    does not parse. Whether what it says is usable is for estimate.py to
    decide."""
    try:
        return _read_json(config_path(checkpoint_dir))
    except (OSError, ValueError, RecursionError):
        return None


def write_config(checkpoint_dir, payload):
    """Write the saved models record atomically. Raises OSError on failure."""
    _atomic_write_json(config_path(checkpoint_dir), payload)


def read_record(checkpoint_dir, unit_id):
    """Return the unit's record, or None if it is absent or unusable.

    Every failure mode collapses to None on purpose: a missing file, a file
    truncated by a killed process, a hand-edited file with a bogus status. The
    caller then treats the unit as pending, which is always the safe answer.

    A record that names a different unit_id also counts as absent. Before ids
    with uppercase letters were tagged, `Alpha` wrote `Alpha.json`; on a
    filesystem that ignores case, opening `alpha.json` reads that file. The
    unit_id check stops such a leftover record from marking `alpha` done.
    """
    path = record_path(checkpoint_dir, unit_id)
    try:
        data = _read_json(path)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
        return None
    if data.get("unit_id") != unit_id:
        return None
    return data


def record(checkpoint_dir, unit_id, status, artifact=None, reason=None, worker=None):
    """Write (or overwrite) a unit's record atomically."""
    if status not in STATUSES:
        raise ValueError("status must be one of {}".format(", ".join(STATUSES)))
    prior = read_record(checkpoint_dir, unit_id) or {}
    attempt = prior.get("attempt", 0)
    if status in ("written", "failed"):
        attempt = attempt + 1
    # `attempt` counts both the writer's `written` and the verifier's `failed`,
    # so one rejected pair adds 2. `failures` counts verifier rejections only,
    # once each, and is what the attempt cap reads. claude-config's copy writes
    # the same field, so a batch resumed under either copy keeps its count.
    failures = failure_count(prior) + (1 if status == "failed" else 0)
    payload = {
        "unit_id": unit_id,
        "status": status,
        "attempt": attempt,
        "failures": failures,
        "updated_at": now_iso(),
    }
    for key, value in (("artifact", artifact), ("reason", reason), ("worker", worker)):
        if value is not None:
            payload[key] = value
    # Carry forward the facts that outlive a status change. The verifier records
    # `--status verified --reason ...` and no `--artifact`, so without this the
    # writer's artifact path is dropped at the exact moment the unit succeeds,
    # and a finished batch has nothing on disk saying where its outputs went.
    # `reason` is deliberately NOT inherited: it describes the current status, so
    # a retry recording `written` must not keep the previous failure's text.
    for key in ("artifact", "worker"):
        if key not in payload and key in prior:
            payload[key] = prior[key]
    # The last rejection's reason does outlive a retry's `written`: a resumed
    # writer needs it, and `failures` hands it to batch.js.
    if status == "failed" and reason is not None:
        payload["last_rejection"] = reason
    elif "last_rejection" in prior:
        payload["last_rejection"] = prior["last_rejection"]
    _atomic_write_json(record_path(checkpoint_dir, unit_id), payload)
    return payload


def failure_count(rec):
    """The record's count of verifier rejections. Anything but a whole number
    of zero or more, such as a hand-edited string, reads as 0."""
    value = (rec or {}).get("failures", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def load_units(units_path):
    try:
        units = _read_json(units_path)
    except RecursionError:
        raise ValueError("units file is nested too deeply to read") from None
    if not isinstance(units, list):
        raise ValueError("units file must contain a JSON list")
    seen = set()
    for unit in units:
        if not isinstance(unit, dict) or "id" not in unit:
            raise ValueError("every unit needs an 'id' field: {!r}".format(unit))
        if unit["id"] in seen:
            raise ValueError("duplicate unit id: {!r}".format(unit["id"]))
        seen.add(unit["id"])
    return units


def reduce_dir(checkpoint_dir, units):
    """Fold the checkpoint dir into {unit_id: status} for the given units."""
    state = {}
    for unit in units:
        rec = read_record(checkpoint_dir, unit["id"])
        state[unit["id"]] = rec["status"] if rec else "pending"
    return state


def _check_cap(max_attempts):
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a whole number of at least 1")


def _unfinished(checkpoint_dir, units):
    """Yield (unit, record or None) for every unit that is not verified."""
    for unit in units:
        rec = read_record(checkpoint_dir, unit["id"])
        if not rec or rec["status"] != DONE:
            yield unit, rec


def capped(checkpoint_dir, units, max_attempts=DEFAULT_MAX_ATTEMPTS):
    """Units that are not verified and have max_attempts or more rejections."""
    _check_cap(max_attempts)
    return [u for u, rec in _unfinished(checkpoint_dir, units) if failure_count(rec) >= max_attempts]


def todo(checkpoint_dir, units, limit=None, max_attempts=DEFAULT_MAX_ATTEMPTS):
    """Units that are not verified and still under the attempt cap.

    A unit the verifier rejected max_attempts times, across every run, is
    left out; `capped` lists those. Raise the cap or delete the unit's record
    to run it again.
    """
    _check_cap(max_attempts)
    pending = [u for u, rec in _unfinished(checkpoint_dir, units) if failure_count(rec) < max_attempts]
    if limit is not None:
        pending = pending[:limit]
    return pending


def failure_counts(checkpoint_dir, units):
    """{id: {"failures": n, "reason": text}} for unfinished units with a
    rejection on record. batch.js takes this as priorFailures, so a resumed
    unit continues its attempt count instead of starting at 1. The reason is
    the last rejection's, or None if none was recorded.
    """
    out = {}
    for unit, rec in _unfinished(checkpoint_dir, units):
        count = failure_count(rec)
        if count:
            reason = rec.get("reason") if rec["status"] == "failed" else rec.get("last_rejection")
            if not isinstance(reason, str):
                reason = None
            out[unit["id"]] = {"failures": count, "reason": reason}
    return out


def report(checkpoint_dir, units):
    state = reduce_dir(checkpoint_dir, units)
    buckets = {"verified": [], "written": [], "failed": [], "pending": []}
    for unit in units:
        buckets[state[unit["id"]]].append(unit["id"])
    failures = []
    for unit_id in buckets["failed"]:
        rec = read_record(checkpoint_dir, unit_id) or {}
        failures.append({"id": unit_id, "reason": rec.get("reason", "no reason recorded")})
    return {
        "total": len(units),
        "verified": len(buckets["verified"]),
        "complete": len(buckets["verified"]) == len(units),
        "ids": buckets,
        "failures": failures,
    }


def claim(checkpoint_dir, unit_id, lease_seconds=DEFAULT_LEASE_SECONDS, owner=None):
    """Take an exclusive lease on a unit. Returns True if this caller won it.

    Only needed when two runs of the same batch could overlap. A single
    workflow run dispatches each unit once, so it does not need this. Leases go
    stale rather than deadlocking: a lock older than `lease_seconds` is assumed
    to belong to a process that died and gets stolen. The age comes from the
    lock file's modified time, not its contents, so a lock a winner has created
    but not yet written still counts as held, and a hand-edited `at` cannot
    hold a unit forever. A file time a full lease or more ahead of the
    clock is bogus and counts as stale, so a shorter lease gives up such a
    lock sooner. A lock whose time cannot be read is left
    alone, so the caller loses unless the lock is already gone. Two callers
    that judge the same lock stale at the same moment can both win; the
    lease length does not prevent that. Keep the lease above a unit's run
    time so a live holder is not judged stale.

    Creating the lock retries a few times while Windows reports it in use,
    then raises PermissionError. A directory the caller cannot write to
    raises too, rather than passing for a lock someone else holds. One
    exception: if a stale lock is already there and the caller cannot delete
    it, the claim returns False as if the lock were held, on every call.
    """
    path = lock_path(checkpoint_dir, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # An owner is stored as given, as a string, so release can name it. Only
    # a missing owner falls back to the pid.
    body = json.dumps({"owner": str(os.getpid()) if owner is None else str(owner), "at": time.time()})
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY

    def create():
        return _retry_while_in_use(lambda: os.open(str(path), flags))

    try:
        fd = create()
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(str(path))
        except OSError:
            # The lock vanished or is being deleted. Its age is unknown, so
            # do not unlink it: that could remove a lock a winner just made.
            age = None
        if age is not None:
            # A zero-second lease is always stealable, even when the file time
            # reads a little ahead of the clock.
            if lease_seconds > 0 and -lease_seconds < age < lease_seconds:
                return False
            try:
                os.unlink(str(path))
            except OSError:
                pass
        try:
            fd = create()
        except FileExistsError:
            return False
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    return True


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def _lock_owner(path):
    """Read the lock at path. Returns (identity, owner), or None if unreadable.

    The identity is taken before the read. The caller compares it with a
    second stat after the read, so a file swapped in between is caught.
    """
    try:
        seen = _identity(os.stat(str(path)))
        with open(str(path), "r", encoding="utf-8") as fh:
            body = fh.read()
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None
    owner = data.get("owner") if isinstance(data, dict) else None
    return (seen, owner) if isinstance(owner, str) else None


def release(checkpoint_dir, unit_id, owner=None):
    """Drop a unit's lease. Returns True if this call removed the lock file.

    With an owner, the lock goes only if its owner field matches, compared as
    a string the way claim stores it. A holder whose stale lease was stolen
    then leaves the thief's lock alone, so a third run cannot claim a unit the
    thief is still working on. The release reads the lock, compares the
    owner, checks the lock is still that same file (inode, file time, size),
    and only then deletes it.

    This copy's claim deletes a stale lock without the steal guard
    claude-config's copy takes, so a steal that lands in the moment between
    that check and the delete can still lose its fresh lock. That is the same
    race claim already documents for two stealers, and it needs a lease that
    ran out while its holder was finishing.

    Without an owner, the lock goes whoever holds it, as before ELI-917. That
    keeps old callers working and keeps the bug for them, so pass the owner
    you claimed with.

    Returns False if there was no lock file, the owner did not match, the
    lock is mid-write or unreadable, it changed between the read and the
    delete, or the file could not be removed.
    """
    path = lock_path(checkpoint_dir, unit_id)
    if owner is None:
        try:
            os.unlink(str(path))
            return True
        except OSError:
            return False
    read = _lock_owner(path)
    if read is None or read[1] != str(owner):
        return False
    try:
        if _identity(os.stat(str(path))) != read[0]:
            return False
        os.unlink(str(path))
    except OSError:
        return False
    return True


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    def add_dir(p):
        p.add_argument("--dir", required=True, help="checkpoint directory")

    p_todo = sub.add_parser("todo", help="print units that are not verified")
    add_dir(p_todo)
    p_todo.add_argument("--units", required=True)
    p_todo.add_argument("--limit", type=_at_least(0), default=None)
    p_todo.add_argument("--max-attempts", type=_at_least(1), default=DEFAULT_MAX_ATTEMPTS,
                        help="leave out units the verifier rejected this many times (default %(default)s)")

    p_fail = sub.add_parser("failures", help="print rejection counts of unfinished units")
    add_dir(p_fail)
    p_fail.add_argument("--units", required=True)

    p_rec = sub.add_parser("record", help="write one unit's status")
    add_dir(p_rec)
    p_rec.add_argument("--id", required=True)
    p_rec.add_argument("--status", required=True, choices=STATUSES)
    p_rec.add_argument("--artifact", default=None)
    p_rec.add_argument("--reason", default=None)
    p_rec.add_argument("--worker", default=None)

    p_rep = sub.add_parser("report", help="print a summary of the batch")
    add_dir(p_rep)
    p_rep.add_argument("--units", required=True)
    p_rep.add_argument("--strict", action="store_true",
                       help="exit 1 unless every unit is verified")

    p_claim = sub.add_parser("claim", help="take an exclusive lease on a unit")
    add_dir(p_claim)
    p_claim.add_argument("--id", required=True)
    p_claim.add_argument("--lease-seconds", type=_at_least(0), default=DEFAULT_LEASE_SECONDS)
    p_claim.add_argument("--owner", default=None)

    p_rel = sub.add_parser("release", help="drop a lease")
    add_dir(p_rel)
    p_rel.add_argument("--id", required=True)
    p_rel.add_argument("--owner", default=None,
                       help="remove the lock only if this owner holds it (pass the owner you claimed with)")

    args = parser.parse_args(argv)

    if args.command == "todo":
        units = load_units(args.units)
        print(json.dumps(todo(args.dir, units, args.limit, args.max_attempts), indent=2))
        skipped = [u["id"] for u in capped(args.dir, units, args.max_attempts)]
        if skipped:
            print("skipped {} unit(s) the verifier rejected {} or more times: {}. Fix the spec, then "
                  "rerun with a higher --max-attempts or delete their records.".format(
                      len(skipped), args.max_attempts, ", ".join(skipped)), file=sys.stderr)
        return 0
    if args.command == "failures":
        print(json.dumps(failure_counts(args.dir, load_units(args.units)), indent=2))
        return 0
    if args.command == "record":
        print(json.dumps(record(args.dir, args.id, args.status, args.artifact,
                                args.reason, args.worker), indent=2))
        return 0
    if args.command == "report":
        summary = report(args.dir, load_units(args.units))
        print(json.dumps(summary, indent=2))
        return 0 if (summary["complete"] or not args.strict) else 1
    if args.command == "claim":
        try:
            won = claim(args.dir, args.id, args.lease_seconds, args.owner)
        except PermissionError as exc:
            # Exit 3: not 1 (held) and not 2 (argparse's usage error).
            print("cannot create the lock: {}".format(exc), file=sys.stderr)
            return 3
        print("claimed" if won else "held-by-another")
        return 0 if won else 1
    if args.command == "release":
        if release(args.dir, args.id, args.owner):
            print("released")
            return 0
        # Exit 4: not 1 (held), 2 (argparse's usage error) or 3 (claim's
        # cannot-create). The lease may have been stolen (another owner's
        # lock, which stays, or no lock at all), or the lock is still there
        # and blocks every other claimant until it goes stale.
        print("cannot release {}: no lock file there, another owner holds it, or it could not be "
              "removed".format(lock_path(args.dir, args.id)), file=sys.stderr)
        return 4
    return 2


if __name__ == "__main__":
    sys.exit(_main())
