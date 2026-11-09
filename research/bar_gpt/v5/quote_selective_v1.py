"""Plan-first candidate caching and immutable selective payload publication.

The frozen ordinal inventory nominates source issue intervals before payloads
are decoded. Noncandidate payloads still receive full SHA256 verification and
independent hardlink readback. Completed prior v2 receipts may be reused only
after their original ancestry and every reused payload hash are checked.
"""

from collections import Counter, OrderedDict
from copy import deepcopy
from datetime import datetime
import hashlib
import inspect
from pathlib import Path

import numpy as np

from .quote_candidates_v1 import statement, file_candidate
from .quote_quality_v1 import POLICY
from .quote_rebuild_v1 import (
    NY,
    SESSION_LOCKS,
    CACHE_WRITER,
    session_batches,
)
from .quote_repair_v1 import (
    GuardOnly,
    read_json,
    verify_seal,
    safe_file,
    publish_file,
    phase_one_payload,
    phase_two_payload,
)
from .shards.contract import digest, write_json
from .shards.balanced_plan import ticker_bounds
from .phase2_shards.contract import BAR, FRAMES


class Selector:
    """Worker-owned transport with shared, source-bound session candidate cache."""

    def __init__(self, rebuilder):
        self.rebuilder = rebuilder
        self.local = OrderedDict()
        self.binding = digest(
            [
                POLICY,
                inspect.getsource(statement),
                rebuilder.authority["dictionary_hash"],
            ]
        )
        with CACHE_WRITER, rebuilder.db:
            rebuilder.db.execute(
                "CREATE TABLE IF NOT EXISTS selective_intervals(binding TEXT,ticker TEXT,day TEXT,source_hash TEXT,payload TEXT,PRIMARY KEY(binding,ticker,day,source_hash)) WITHOUT ROWID"
            )

    def intervals(self, ticker, begin, end):
        """Return ordered candidate intervals covering the entire saved context.

        The per-ticker lock coalesces overlapping contexts across all workers.
        Frozen source-row hashes prevent a cached day from another ordinal plan
        being accepted merely because its ticker/date matches.
        """
        import json

        b = self.rebuilder
        if ticker not in b.bounds:
            root = b.root if b.phase == 1 else b.root / "inventory"
            b.bounds[ticker] = ticker_bounds(root, b.jobs[ticker])
            if len(b.bounds) > 4:
                evicted, _ = b.bounds.popitem(last=False)
                if hasattr(b, "groups"):
                    b.groups.pop(evicted, None)
        first = datetime.fromtimestamp(int(begin) / 1e6, NY).date().isoformat()
        last = datetime.fromtimestamp(int(end - 1) / 1e6, NY).date().isoformat()
        bounds = [
            row for row in b.bounds[ticker] if first <= row["source_date"] <= last
        ]
        if not bounds:
            raise ValueError("Saved file clocks are outside frozen source sessions")
        keys = {
            row["source_date"]: (ticker, row["source_date"], digest(row))
            for row in bounds
        }
        stripe = int(digest(ticker)[:8], 16) % len(SESSION_LOCKS)
        with SESSION_LOCKS[stripe]:
            missing = []
            for row in bounds:
                day = row["source_date"]
                key = keys[day]
                if key not in self.local:
                    cached = b.db.execute(
                        "SELECT payload FROM selective_intervals WHERE binding=? AND ticker=? AND day=? AND source_hash=?",
                        (self.binding, *key),
                    ).fetchone()
                    if cached:
                        self.local[key] = json.loads(cached[0])
                    else:
                        missing.append(row)
            for group in session_batches(missing, max_events=1_000_000, max_days=14):
                table = b.query(
                    statement(ticker, group, b.authority["rules"]),
                    "selective-" + digest([ticker, group, self.binding])[:24],
                    allow_empty=True,
                )
                grouped = {row["source_date"]: [] for row in group}
                for row in table.to_dicts():
                    # An ordinal UNION can read intervening unrequested sessions.
                    # Their evidence is harmless but not cached under absent keys.
                    if row["source_date"] in grouped:
                        grouped[row["source_date"]].append(row)
                with CACHE_WRITER, b.db:
                    for day, rows in grouped.items():
                        key = keys[day]
                        b.db.execute(
                            "INSERT OR IGNORE INTO selective_intervals VALUES(?,?,?,?,?)",
                            (self.binding, *key, json.dumps(rows)),
                        )
                        self.local[key] = rows
                        b.metrics["candidate_sessions"] += 1
                        b.metrics["candidate_intervals"] += len(rows)
            result = [
                row
                for bound in bounds
                for row in self.local[keys[bound["source_date"]]]
            ]
            # Trim only AFTER assembling this context; it can legitimately span
            # more sessions than a cache's ordinary steady-state capacity.
            while len(self.local) > 2048:
                self.local.popitem(last=False)
            return sorted(result, key=lambda row: row["begin_us"])


def packed_bounds(path, descriptors):
    """Read only first/last Int64 clocks of sorted BAR segments, not every bar.

    Packed source descriptors have no timestamp bounds. Two eight-byte reads per
    segment provide a conservative file envelope. Full payload hashing remains
    mandatory before either copying or linking it into the repaired release.
    """
    starts, ends = [], []
    with path.open("rb") as handle:
        size = path.stat().st_size
        for item in descriptors:
            rows = int(np.prod(item["shape"]))
            offset = int(item["offset"])
            if not rows:
                continue
            if (
                item["kind"] != "bar"
                or offset < 0
                or offset + rows * BAR.itemsize > size
            ):
                raise ValueError("Invalid packed bar geometry")
            field = BAR.fields["end_us"][1]
            handle.seek(offset + field)
            first = int.from_bytes(handle.read(8), "little", signed=True)
            handle.seek(offset + (rows - 1) * BAR.itemsize + field)
            last = int.from_bytes(handle.read(8), "little", signed=True)
            if first <= 0 or last < first:
                raise ValueError("Invalid packed clock bounds")
            starts.append(first - FRAMES[item["frame"]])
            ends.append(last)
    return (min(starts), max(ends)) if starts else (None, None)


def migrate(source, output, relative, binding, phase, selector, reuse_root=None):
    """Select first, then decode only candidate files; keep original receipt ancestry."""
    original = read_json(source / relative)
    key = "receipt_hash" if phase == 1 else "hash"
    verify_seal(original, key)
    target = output / relative
    if target.exists():
        receipt = read_json(target)
        verify_seal(receipt, key)
        if phase == 1 and receipt["binding"] != binding:
            raise ValueError("Selective resume binding changed")
        if receipt["quote_repair"]["parent_receipt"] != original[key]:
            raise ValueError("Selective resume ancestry changed")
        for item in receipt["files"]:
            from .shards.contract import file_hash

            if file_hash(safe_file(target.parent, item["name"])) != item["sha256"]:
                raise ValueError("Selective resumed payload changed")
        return receipt
    base, base_root = original, source
    if reuse_root is not None and (reuse_root / relative).exists():
        prior = read_json(reuse_root / relative)
        verify_seal(prior, key)
        if (
            prior["quote_repair"]["parent_receipt"] != original[key]
            or prior["quote_repair"]["policy"]["version"]
            != "bargpt-paired-quote-quarantine-v2"
        ):
            raise ValueError("Reusable v2 receipt has foreign ancestry/policy")
        if [i["name"] for i in prior["files"]] != [
            i["name"] for i in original["files"]
        ]:
            raise ValueError("Reusable v2 payload inventory differs")
        base, base_root = prior, reuse_root
    receipt = deepcopy(original)
    totals = Counter()
    changed_files = 0
    coverage = np.array(base.get("valid_bars_by_channel", []), dtype=np.int64)
    if phase == 1 and coverage.shape != (18,):
        raise ValueError("Selective publication requires sealed per-channel coverage")
    target.parent.mkdir(parents=True, exist_ok=True)
    before = selector.rebuilder.metrics.copy()
    for item, input_item in zip(receipt["files"], base["files"]):
        if {k: v for k, v in item.items() if k != "sha256"} != {
            k: v for k, v in input_item.items() if k != "sha256"
        }:
            raise ValueError("Reusable payload changed geometry")
        src = safe_file((base_root / relative).parent, input_item["name"])
        dst = safe_file(target.parent, item["name"])
        descriptors = [
            s for s in original.get("segments", []) if s["name"] == item["name"]
        ]
        begin, end = item.get("first_start_us"), item.get("last_end_us")
        if phase == 2 and descriptors and src.suffix == ".bin":
            begin, end = packed_bounds(src, descriptors)
        candidate = True
        if phase == 2 and not descriptors:
            candidate = False
        elif begin is not None and end is not None:
            intervals = selector.intervals(original["ticker"], begin, end)
            candidate = file_candidate(begin, end, intervals)
        payload = None
        if candidate:
            # Hashing is independent of selection; candidate bytes are reused by
            # decode/publication rather than reread from disk at every stage.
            from .quote_repair_io_v1 import payload_io

            with payload_io():
                serialized = src.read_bytes()
            if (
                len(serialized) != input_item["bytes"]
                or hashlib.sha256(serialized).hexdigest() != input_item["sha256"]
            ):
                raise ValueError("Candidate source payload changed")
            builder = GuardOnly() if base_root != source else selector.rebuilder
            if phase == 1:
                payload, counts = phase_one_payload(
                    src,
                    original["ticker"],
                    builder,
                    serialized=serialized,
                    coverage=coverage,
                    coverage_delta=True,
                )
            else:
                payload, counts = phase_two_payload(
                    src, descriptors, original["ticker"], builder, serialized=serialized
                )
            totals.update(counts)
            totals["candidate_files"] += 1
            revised, _ = publish_file(
                src, dst, input_item, payload, verified_source=serialized
            )
        else:
            revised, _ = publish_file(src, dst, input_item)
            totals["noncandidate_files"] += 1
        totals["verified_bytes"] += input_item["bytes"]
        changed_files += int(revised["sha256"] != item["sha256"])
        item.update(revised)
    hashes = {item["name"]: item["sha256"] for item in receipt["files"]}
    if phase == 1:
        if np.any(coverage < 0):
            raise ValueError("Selective coverage reconciliation became negative")
        receipt.update(
            binding=binding,
            identity=digest([binding, original["identity"]]),
            valid_bars_by_channel=coverage.tolist(),
        )
    else:
        for descriptor in [*receipt["segments"], *receipt.get("block_segments", [])]:
            descriptor["sha256"] = hashes[descriptor["name"]]
    totals.update(selector.rebuilder.metrics - before)
    receipt["bytes"] = sum(item["bytes"] for item in receipt["files"])
    receipt["quote_repair"] = dict(
        policy=POLICY,
        parent_receipt=original[key],
        counts=dict(totals),
        changed_files=changed_files,
        algorithm="source-first-selective-v1",
        candidate_binding=selector.binding,
    )
    if base_root != source:
        receipt["quote_repair"]["reused_receipt"] = base[key]
    receipt[key] = digest({k: v for k, v in receipt.items() if k != key})
    write_json(target, receipt)
    return receipt
