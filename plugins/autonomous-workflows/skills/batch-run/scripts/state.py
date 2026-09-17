"""Durable per-unit checkpoint store for batch-run.

One small file per work unit under a checkpoint directory, written atomically
(temp file plus `os.replace`, which is atomic on Windows and POSIX). Parallel
workers never touch the same file, so there is no shared-file race and no
locking needed for the record path.

The invariant this module exists to enforce:

    A unit is done if and only if its record file exists, parses, and carries
    status "verified". Missing, malformed, "written", and "failed" all mean
    pending.

That is why a killed session needs no error handler. Failure is the absence of
a success record, not an event someone has to catch on the way down.

CLI:
    python state.py todo    --units units.json --dir checkpoint [--limit N]
    python state.py record  --dir checkpoint --id ID --status verified [...]
    python state.py report  --units units.json --dir checkpoint [--strict]
    python state.py claim   --dir checkpoint --id ID [--lease-seconds 1800]
    python state.py release --dir checkpoint --id ID
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("pending", "written", "verified", "failed")
DONE = "verified"
DEFAULT_LEASE_SECONDS = 1800

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SLUG = 80


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug_for(unit_id):
    """Map an arbitrary unit id onto a safe, collision-free filename stem.

    Ids come from user data and may contain slashes, spaces, or path tricks.
    Anything that is not already filename-safe gets sanitized and tagged with a
    hash of the original, so two different ids can never land on one file.
    """
    if not isinstance(unit_id, str) or not unit_id.strip():
        raise ValueError("unit id must be a non-empty string")
    safe = _UNSAFE.sub("_", unit_id)
    if safe != unit_id or safe in (".", "..") or len(safe) > _MAX_SLUG:
        digest = hashlib.sha1(unit_id.encode("utf-8")).hexdigest()[:8]
        safe = "{}.{}".format(safe[:_MAX_SLUG], digest)
    return safe


def record_path(checkpoint_dir, unit_id):
    return Path(checkpoint_dir) / "{}.json".format(slug_for(unit_id))


def lock_path(checkpoint_dir, unit_id):
    return Path(checkpoint_dir) / "{}.lock".format(slug_for(unit_id))


def _atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / "{}.{}.tmp".format(path.name, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_record(checkpoint_dir, unit_id):
    """Return the unit's record, or None if it is absent or unusable.

    Every failure mode collapses to None on purpose: a missing file, a file
    truncated by a killed process, a hand-edited file with a bogus status. The
    caller then treats the unit as pending, which is always the safe answer.
    """
    path = record_path(checkpoint_dir, unit_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") not in STATUSES:
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
    payload = {
        "unit_id": unit_id,
        "status": status,
        "attempt": attempt,
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
    _atomic_write_json(record_path(checkpoint_dir, unit_id), payload)
    return payload


def load_units(units_path):
    with open(units_path, "r", encoding="utf-8") as fh:
        units = json.load(fh)
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


def todo(checkpoint_dir, units, limit=None):
    state = reduce_dir(checkpoint_dir, units)
    pending = [u for u in units if state[u["id"]] != DONE]
    if limit is not None:
        pending = pending[:limit]
    return pending


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
    to belong to a process that died and gets stolen.
    """
    path = lock_path(checkpoint_dir, unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"owner": owner or str(os.getpid()), "at": time.time()})
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags)
    except FileExistsError:
        try:
            age = time.time() - json.loads(path.read_text(encoding="utf-8"))["at"]
        except (OSError, ValueError, KeyError, TypeError):
            age = lease_seconds + 1
        # Strictly less than, so a zero-second lease is always stealable rather
        # than depending on whether the clock ticked between two calls.
        if age < lease_seconds:
            return False
        try:
            os.unlink(str(path))
        except OSError:
            pass
        try:
            fd = os.open(str(path), flags)
        except FileExistsError:
            return False
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    return True


def release(checkpoint_dir, unit_id):
    try:
        os.unlink(str(lock_path(checkpoint_dir, unit_id)))
        return True
    except OSError:
        return False


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    def add_dir(p):
        p.add_argument("--dir", required=True, help="checkpoint directory")

    p_todo = sub.add_parser("todo", help="print units that are not verified")
    add_dir(p_todo)
    p_todo.add_argument("--units", required=True)
    p_todo.add_argument("--limit", type=int, default=None)

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
    p_claim.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    p_claim.add_argument("--owner", default=None)

    p_rel = sub.add_parser("release", help="drop a lease")
    add_dir(p_rel)
    p_rel.add_argument("--id", required=True)

    args = parser.parse_args(argv)

    if args.command == "todo":
        print(json.dumps(todo(args.dir, load_units(args.units), args.limit), indent=2))
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
        won = claim(args.dir, args.id, args.lease_seconds, args.owner)
        print("claimed" if won else "held-by-another")
        return 0 if won else 1
    if args.command == "release":
        release(args.dir, args.id)
        print("released")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
