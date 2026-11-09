"""Resumable full-payload quote audit and immutable quarantine migration.

Run on the workstation with ``python -B -m research.bar_gpt.v5.quote_repair_v1
--source <old-root> --output <distinct-root> --source-revision <commit>``.
Every sealed payload is SHA-verified and every stored bar examined. Unchanged
files are hardlinked (never subsequently written); changed files are new files.
Original datasets and running readers are untouched. Phase two MUST be drained.
No GPU, pandas, canonical writes, market-event fallbacks or price clipping occur.

The output is not eligible for training/generation until repair-result.json is
sealed as complete. Progress and rejection reasons are persistent runtime data.
An output STOP drains work without deleting completed receipt publications.
"""

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import traceback

import numpy as np
import psutil

from .quote_quality_v1 import POLICY, sanitize, neighbor_spikes
from .shards.contract import digest, file_hash, write_json
from .phase2_shards.contract import BAR
from .quote_rebuild_v1 import Rebuilder, QUOTE
from .quote_repair_io_v1 import payload_io, TIMES
from .phase2_shards.training_guard import TrainingGuard

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
LOCAL = threading.local()
BUILDERS = []
BUILDERS_LOCK = threading.Lock()


class GuardOnly:
    """No canonical reads: tighten the bar guard on released repaired payloads.

    Existing v2 quote fields already have canonical condition reconstruction.
    A stricter v3 quarantine can only remove evidence, so rebuilding those fields
    again would add queries without improving source evidence. The CLI requires
    a complete v2 repair release before this explicit mode can be used.
    """

    bar_guard_only = True
    actions = ()

    def __init__(self):
        self.metrics = Counter()

    def get(self, ticker, frame, ends):
        """Return QUOTE[N] with rebuild=0; all existing quote bytes are retained."""
        return np.zeros(len(ends), dtype=QUOTE)

    def close(self):
        """No transport or cache is created by this explicit no-query mode."""
        return None


def read_json(path):
    """Read a small sealed metadata object; never infer an absent receipt."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_seal(receipt, key):
    """Reject corrupt parent or migrated receipt before trusting payload paths."""
    if receipt[key] != digest({k: v for k, v in receipt.items() if k != key}):
        raise ValueError("Receipt seal mismatch")


def safe_file(directory, name):
    """Limit receipt-supplied filenames to one owned publication directory."""
    if not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("Unsafe receipt payload name")
    return directory / name


@payload_io()
def publish_file(source, target, original, payload=None, *, verified_source=None):
    """Verify source, then hardlink unchanged bytes or fsync a distinct replacement.

    Existing hardlinks are never opened for writing. Atomic rename permits safe
    recovery of a task-owned .partial file after interruption. Returned metadata
    retains the source's array bounds and row counts with the new byte hash.
    """
    data = source.read_bytes() if verified_source is None else verified_source
    if (
        len(data) != original["bytes"]
        or hashlib.sha256(data).hexdigest() != original["sha256"]
    ):
        raise ValueError(f"Source payload changed: {source}")
    changed = payload is not None and payload != data
    content = payload if changed else data
    temporary = target.with_name(target.name + ".partial")
    if temporary.exists():
        temporary.unlink()  # Only an unpublished task-owned temporary file.
    if changed:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        os.link(
            source, temporary
        )  # Same-volume requirement; no silent full-copy fallback.
    temporary.replace(target)
    item = dict(
        original, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()
    )
    if file_hash(target) != item["sha256"]:
        raise ValueError("Published payload readback failed")
    return item, changed


def load_payload(path, serialized=None):
    """Fully read an NPY payload at background I/O priority; return its CPU array."""
    if serialized is not None:
        import io

        return np.load(io.BytesIO(serialized), allow_pickle=False)
    with payload_io():
        return np.load(path, allow_pickle=False)


def phase_one_payload(path, ticker, rebuilder, *, serialized=None, coverage=None):
    """Audit SAMPLE[N,64,18]; return replacement bytes only when a bar is rejected."""
    import io
    from .shards.phase1_contract import SAMPLE_DTYPE

    data = load_payload(path, serialized)
    if data.dtype != SAMPLE_DTYPE:
        raise ValueError("Unexpected phase-one record schema")
    if getattr(rebuilder, "bar_guard_only", False):
        values, masks, counts = sanitize(data["values"], data["valid"])
        if coverage is not None:
            coverage += masks.sum(axis=(0, 1), dtype=np.uint64)
        counts.update(
            changed_bars=counts["quarantined"],
            rebuilt_bars=0,
            neighbor_candidates_before=int(
                neighbor_spikes(data["values"], data["valid"]).sum()
            ),
            neighbor_candidates_after=int(neighbor_spikes(values, masks).sum()),
        )
        if not np.isfinite(data["values"][..., :6]).all():
            raise ValueError("Nonfinite trade evidence; guard cannot modify trades")
        if not counts["changed_bars"]:
            return None, counts
        data["values"][..., 6:], data["valid"][..., 6:] = (
            values[..., 6:],
            masks[..., 6:],
        )
        buffer = io.BytesIO()
        np.save(buffer, data, allow_pickle=False)
        return buffer.getvalue(), counts
    from .shards.storage import adjust_splits

    # Rebuild ALL quotes, not just spectacular ratios: a rejected condition may
    # also have changed a plausible open, count or carry. Trades remain original.
    revised = np.zeros_like(data)
    rebuild = np.zeros(data["end_us"].shape, dtype=bool)
    revised["end_us"] = data["end_us"]
    for frame in np.unique(data["timeframe_us"]):
        take = data["timeframe_us"] == frame
        clocks = data["end_us"][take]
        quotes = rebuilder.get(ticker, int(frame), clocks.reshape(-1)).reshape(
            clocks.shape
        )
        revised["values"][take, :, 6:] = quotes["values"]
        revised["valid"][take, :, 6:] = quotes["valid"]
        rebuild[take] = quotes["rebuild"].astype(bool)
    actions = [row for row in rebuilder.actions if row["provider_ticker"] == ticker]
    adjust_splits(
        revised, actions
    )  # Rebuilt raw quote fields use each saved sample's original basis.
    revised["values"][..., :6], revised["valid"][..., :6] = (
        data["values"][..., :6],
        data["valid"][..., :6],
    )
    # No-change source intervals retain exact original Float32 values, avoiding
    # gratuitous rounding differences or queries for unaffected overlapping bars.
    revised["values"][..., 6:][~rebuild] = data["values"][..., 6:][~rebuild]
    revised["valid"][..., 6:][~rebuild] = data["valid"][..., 6:][~rebuild]
    values, masks, counts = sanitize(revised["values"], revised["valid"])
    if coverage is not None:
        coverage += masks.sum(axis=(0, 1), dtype=np.uint64)
    changed = np.any(values[..., 6:] != data["values"][..., 6:], axis=-1) | np.any(
        masks[..., 6:] != data["valid"][..., 6:], axis=-1
    )
    counts.update(rebuilt_bars=int(rebuild.sum()), changed_bars=int(changed.sum()))
    counts.update(
        {
            "before_" + k: v
            for k, v in sanitize(data["values"], data["valid"])[2].items()
        }
    )
    counts["neighbor_candidates_before"] = int(
        neighbor_spikes(data["values"], data["valid"]).sum()
    )
    counts["neighbor_candidates_after"] = int(neighbor_spikes(values, masks).sum())
    if not np.isfinite(data["values"][..., :6]).all():
        raise ValueError("Nonfinite trade evidence; quote repair cannot modify trades")
    if not changed.any():
        return None, counts
    # Only quote columns change; sample identity, order, time and trade bytes stay exact.
    data["values"][..., 6:], data["valid"][..., 6:] = values[..., 6:], masks[..., 6:]
    if sanitize(data["values"], data["valid"])[2]["quarantined"]:
        raise ValueError("Quote repair is not idempotent")
    buffer = io.BytesIO()
    np.save(buffer, data, allow_pickle=False)
    return buffer.getvalue(), counts


def phase_two_payload(path, descriptors, ticker, rebuilder, *, serialized=None):
    """Audit every BAR segment in one <=16 MiB packed payload, preserving offsets.

    Decision indices, context clocks, padding bytes and unrelated descriptors
    remain byte-identical. Legacy NPY bars use the same pure policy.
    """
    if path.suffix == ".npy":
        import io

        data = load_payload(path, serialized)
        if data.dtype != BAR:
            return None, Counter()  # Decision arrays have no quote inputs.
        if len(descriptors) != 1:
            raise ValueError("Legacy bar file needs one unambiguous frame descriptor")
        from .phase2_shards.contract import FRAMES

        quotes = rebuilder.get(ticker, FRAMES[descriptors[0]["frame"]], data["end_us"])
        revised = data.copy()
        rebuild = quotes["rebuild"].astype(bool)
        revised["values"][..., 6:][rebuild] = quotes["values"][rebuild]
        revised["valid"][..., 6:][rebuild] = quotes["valid"][rebuild]
        values, valid, counts = sanitize(revised["values"], revised["valid"])
        changed = np.any(values[..., 6:] != data["values"][..., 6:], axis=-1) | np.any(
            valid[..., 6:] != data["valid"][..., 6:], axis=-1
        )
        counts.update(rebuilt_bars=int(rebuild.sum()), changed_bars=int(changed.sum()))
        counts.update(
            {
                "before_" + k: v
                for k, v in sanitize(data["values"], data["valid"])[2].items()
            }
        )
        if not changed.any():
            return None, counts
        data["values"][..., 6:], data["valid"][..., 6:] = (
            values[..., 6:],
            valid[..., 6:],
        )
        buffer = io.BytesIO()
        np.save(buffer, data, allow_pickle=False)
        return buffer.getvalue(), counts
    if not descriptors:
        return None, Counter()
    if serialized is None:
        with payload_io():
            serialized = path.read_bytes()
    data = bytearray(serialized)
    totals = Counter()
    intervals = []
    for descriptor in descriptors:
        if descriptor["kind"] != "bar":
            raise ValueError("Non-bar descriptor selected for quote repair")
        shape = tuple(descriptor["shape"])
        offset, length = descriptor["offset"], int(np.prod(shape)) * BAR.itemsize
        if offset < 0 or offset + length > len(data):
            raise ValueError("Bar descriptor escapes sealed payload")
        if any(offset < end and offset + length > start for start, end in intervals):
            raise ValueError("Overlapping packed bar descriptors")
        intervals.append((offset, offset + length))
        bars = np.ndarray(shape, dtype=BAR, buffer=data, offset=offset)
        from .phase2_shards.contract import FRAMES

        quotes = rebuilder.get(
            ticker, FRAMES[descriptor["frame"]], bars["end_us"].reshape(-1)
        ).reshape(shape)
        revised = bars.copy()
        rebuild = quotes["rebuild"].astype(bool)
        revised["values"][..., 6:][rebuild] = quotes["values"][rebuild]
        revised["valid"][..., 6:][rebuild] = quotes["valid"][rebuild]
        values, masks, counts = sanitize(revised["values"], revised["valid"])
        changed = np.any(values[..., 6:] != bars["values"][..., 6:], axis=-1) | np.any(
            masks[..., 6:] != bars["valid"][..., 6:], axis=-1
        )
        counts.update(rebuilt_bars=int(rebuild.sum()), changed_bars=int(changed.sum()))
        counts.update(
            {
                "before_" + k: v
                for k, v in sanitize(bars["values"], bars["valid"])[2].items()
            }
        )
        totals.update(counts)
        bars["values"][..., 6:], bars["valid"][..., 6:] = (
            values[..., 6:],
            masks[..., 6:],
        )
    return (bytes(data) if totals["changed_bars"] else None), totals


def migrate_receipt(source, output, relative, binding, phase, guard_only=False):
    """Migrate one publication; return bounded evidence to the coordinator.

    Crash recovery trusts only a sealed matching child receipt AND its verified
    payloads. Rejected-bar reason counts are stored per unit, not sampled away.
    """
    began = time.monotonic()
    io_before = getattr(TIMES, "microseconds", 0)
    original = read_json(source / relative)
    key = "receipt_hash" if phase == 1 else "hash"
    verify_seal(original, key)
    target = output / relative
    if target.exists():
        receipt = read_json(target)
        verify_seal(receipt, key)
        if phase == 1 and receipt["binding"] != binding:
            raise ValueError(
                "Repaired receipt belongs to a different producer contract"
            )
        if receipt.get("quote_repair", {}).get("parent_receipt") != original[key]:
            raise ValueError("Resume parent receipt differs")
        for item in receipt["files"]:
            with payload_io():
                if file_hash(safe_file(target.parent, item["name"])) != item["sha256"]:
                    raise ValueError("Resumed repaired payload changed")
        return receipt
    target.parent.mkdir(parents=True, exist_ok=True)
    if not hasattr(LOCAL, "rebuilder"):
        LOCAL.rebuilder = (
            GuardOnly()
            if guard_only
            else Rebuilder(
                output.parent / "quote-repair-cache-v1.sqlite", source, phase, binding
            )
        )
        with BUILDERS_LOCK:
            BUILDERS.append(LOCAL.rebuilder)
    rebuilder = LOCAL.rebuilder
    prior_metrics = rebuilder.metrics.copy()
    receipt, totals, changed_files = deepcopy(original), Counter(), 0
    coverage = np.zeros(18, dtype=np.uint64)
    for item in receipt["files"]:
        src = safe_file((source / relative).parent, item["name"])
        dst = safe_file(target.parent, item["name"])
        # Read/verify the original bytes ONCE, then decode and publish from that
        # same buffer. Every target still receives independent SHA readback.
        with payload_io():
            serialized = src.read_bytes()
            if (
                len(serialized) != item["bytes"]
                or hashlib.sha256(serialized).hexdigest() != item["sha256"]
            ):
                raise ValueError(f"Source payload changed: {src}")
        if phase == 1:
            payload, counts = phase_one_payload(
                src,
                original["ticker"],
                rebuilder,
                serialized=serialized,
                coverage=coverage,
            )
        else:
            descriptors = [s for s in receipt["segments"] if s["name"] == item["name"]]
            payload, counts = phase_two_payload(
                src, descriptors, original["ticker"], rebuilder, serialized=serialized
            )
        revised, changed = publish_file(
            src, dst, item, payload, verified_source=serialized
        )
        item.update(revised)
        changed_files += int(changed)
        totals.update(counts)
    # Descriptors seal the complete physical container, including decision bytes.
    hashes = {item["name"]: item["sha256"] for item in receipt["files"]}
    if phase == 2:
        for item in [*receipt["segments"], *receipt.get("block_segments", [])]:
            item["sha256"] = hashes[item["name"]]
    else:
        receipt["binding"] = binding
        receipt["identity"] = digest([binding, original["identity"]])
        # Sum the exact serialized masks in RAM instead of rereading every file.
        receipt["valid_bars_by_channel"] = coverage.tolist()
    receipt["bytes"] = sum(f["bytes"] for f in receipt["files"])
    totals.update(rebuilder.metrics - prior_metrics)
    totals["payload_io_microseconds"] = getattr(TIMES, "microseconds", 0) - io_before
    totals["processing_microseconds"] = int((time.monotonic() - began) * 1e6)
    receipt["quote_repair"] = dict(
        policy=POLICY,
        parent_receipt=original[key],
        counts=dict(totals),
        changed_files=changed_files,
    )
    if guard_only:
        prior = original["quote_repair"]
        receipt["quote_repair"].update(
            root_parent_receipt=prior.get(
                "root_parent_receipt", prior["parent_receipt"]
            ),
            prior_policy=prior["policy"],
            mode="released-v2-bar-guard-only; no canonical queries",
        )
    receipt[key] = digest({k: v for k, v in receipt.items() if k != key})
    write_json(target, receipt)  # Receipt is the last publication/commit marker.
    return receipt


def prepare(source, output, phase, revision, guard_only=False, repaired_base=None):
    """Bind an immutable parent and explicit quality policy; never mutate it in place."""
    base = Path(__file__).resolve().parent
    parent = read_json(source / ("contract.json" if phase == 1 else "plan.json"))
    if phase == 1:
        if parent["binding"] != digest(parent["contract"]):
            raise ValueError("Parent dataset contract seal mismatch")
    else:
        verify_seal(parent, "hash")
    identity = dict(
        source=str(source.resolve()), parent=digest(parent), phase=phase, policy=POLICY
    )
    if guard_only:
        identity["mode"] = "released-v2-bar-guard-only"
    seal = dict(identity=identity, hash=digest(identity), source_revision=revision)
    path = output / "repair-contract.json"
    if path.exists():
        previous = read_json(path)
        if previous["identity"] != identity:
            raise ValueError("Repair source or policy changed")
        seal["source_revisions"] = list(
            dict.fromkeys(
                [
                    *previous.get("source_revisions", [previous["source_revision"]]),
                    revision,
                ]
            )
        )
    else:
        seal["source_revisions"] = [revision]
    write_json(path, seal)
    if phase == 1:
        contract = deepcopy(parent["contract"])
        contract["quote_quality"] = POLICY
        contract["repair_parent_binding"] = parent["binding"]
        if guard_only:
            contract["repair_root_parent_binding"] = parent["contract"].get(
                "repair_root_parent_binding",
                parent["contract"]["repair_parent_binding"],
            )
        if guard_only and "parent_binding" in contract:
            if repaired_base is None:
                raise ValueError(
                    "Supplement guard upgrade requires its explicit repaired base"
                )
            require_complete(repaired_base)
            contract["repair_prior_cohort_parent"] = contract["parent_binding"]
            contract["parent_binding"] = read_json(repaired_base / "contract.json")[
                "binding"
            ]
        from . import bar_semantics_v1

        contract["code"]["research.bar_gpt.v5.bar_semantics_v1"] = file_hash(
            base / "bar_semantics_v1.py"
        )
        contract["quote_semantics"] = bar_semantics_v1.CONTRACT
        for file in (base / "shards").glob("*.py"):
            if not file.name.startswith("test_"):
                contract["code"][file.name] = file_hash(file)
        contract["code"]["research.bar_gpt.v5.quote_quality_v1"] = file_hash(
            base / "quote_quality_v1.py"
        )
        wrapper = dict(contract=contract, binding=digest(contract))
        if (output / "contract.json").exists() and read_json(output / "contract.json")[
            "binding"
        ] != wrapper["binding"]:
            raise ValueError(
                "Shared producer drift: do not mix contracts in a repaired dataset"
            )
        write_json(output / "contract.json", wrapper)
        return wrapper["binding"]
    from .phase2_shards.plan import PRODUCERS

    plan = deepcopy(parent)
    plan.update(
        quote_quality=POLICY,
        repair_parent_plan=parent["hash"],
        revision=revision,
        code={name: file_hash(base / name) for name in PRODUCERS},
    )
    repaired_parent = repaired_base or source.parent / "shards-2019-2025-v6-quote-v2"
    require_complete(repaired_parent)
    plan["parent_binding_before_repair"] = plan["parent_binding"]
    plan["parent_binding"] = read_json(repaired_parent / "contract.json")["binding"]
    plan["hash"] = digest({k: v for k, v in plan.items() if k != "hash"})
    if (output / "plan.json").exists():
        existing = read_json(output / "plan.json")
        verify_seal(existing, "hash")
        for field in ("code", "parent_binding", "repair_parent_plan", "quote_quality"):
            if existing[field] != plan[field]:
                raise ValueError("Phase-two repair producer/parent drift")
        return existing["hash"]
    write_json(output / "plan.json", plan)
    return plan["hash"]


def require_complete(root):
    """Prevent a growing migrated corpus from entering training or generation."""
    root = Path(root)
    if not (root / "repair-contract.json").exists():
        return  # Original immutable releases retain their existing eligibility.
    path = root / "repair-result.json"
    if not path.exists():
        raise ValueError("Quote migration is not released; wait for its complete audit")
    result = read_json(path)
    verify_seal(result, "hash")
    if (
        result["state"] != "complete"
        or result["contract"] != read_json(root / "repair-contract.json")["hash"]
    ):
        raise ValueError("Incomplete or mismatched quote migration release")


def finalize(source, output, phase, binding, ledger, counts):
    """Release complete payloads with unchanged source plans and block selection.

    The phase-two catalog is copied with SQLite backup, then only receipt hashes
    and byte sizes change. Selection IDs, splits and resume cursors stay exact.
    Metadata links are immutable, and an unfinished release remains gated.
    """
    if ledger.execute("SELECT count(*) FROM work WHERE done=0").fetchone()[0]:
        raise ValueError("Cannot release unfinished receipt work")
    folder = "plan" if phase == 1 else "inventory"
    for path in (source / folder).rglob("*"):
        target = output / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                os.link(path, target)
            if file_hash(path) != file_hash(target):
                raise ValueError("Frozen inventory link mismatch")
    if phase == 1:
        if (source / "plan.json").exists() and not (output / "plan.json").exists():
            os.link(source / "plan.json", output / "plan.json")
        for name in ("result.json", "progress.json"):
            metadata = read_json(source / name)
            metadata["quote_repair"] = dict(binding=binding, counts=counts)
            write_json(output / name, metadata)
    else:
        target = output / "catalog.sqlite"
        if not target.exists():
            with sqlite3.connect(source / "catalog.sqlite") as old, sqlite3.connect(
                target
            ) as new:
                old.backup(new)
        with sqlite3.connect(target) as catalog:
            catalog.execute(
                "ATTACH DATABASE ? AS repair", (str(output / "repair.sqlite"),)
            )
            catalog.executescript("""
                UPDATE units SET hash=(SELECT seal FROM repair.work WHERE mappedkey=units.stream||'/'||printf('%04d',units.unit)),
                  bytes=(SELECT bytes FROM repair.work WHERE mappedkey=units.stream||'/'||printf('%04d',units.unit));
            """)
            if catalog.execute(
                "SELECT count(*) FROM units WHERE hash IS NULL OR bytes IS NULL"
            ).fetchone()[0]:
                raise ValueError("Catalog contains an unmigrated immutable receipt")
        layout = read_json(source / "layout.json")
        layout["plan"] = binding
        write_json(output / "layout.json", layout)
    result = dict(
        state="complete",
        contract=read_json(output / "repair-contract.json")["hash"],
        binding=binding,
        phase=phase,
        counts=counts,
        completed=time.time(),
        receipts=ledger.execute("SELECT count(*) FROM work").fetchone()[0],
    )
    result["hash"] = digest(result)
    write_json(output / "repair-result.json", result)


def main(argv=None):
    """Run a bounded worker pool; progress and ledger survive coordinator failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--training-campaign", type=Path)
    parser.add_argument(
        "--bar-guard-only",
        action="store_true",
        help="Tighten final guards on a complete v2 repair, without canonical queries",
    )
    parser.add_argument("--repaired-base", type=Path)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Pilot receipts only; never publish completion",
    )
    args = parser.parse_args(argv)
    source, output = args.source.resolve(), args.output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Repair requires disjoint sibling dataset roots")
    if not source.is_dir() or not 1 <= args.workers <= 16 or args.limit < 0:
        raise ValueError("Invalid source, worker count or pilot bound")
    phase = 1 if (source / "contract.json").exists() else 2
    if args.bar_guard_only:
        require_complete(source)
        prior_contract = read_json(source / "repair-contract.json")
        if (
            prior_contract["hash"] != digest(prior_contract["identity"])
            or prior_contract["identity"]["policy"]["version"]
            != "bargpt-paired-quote-quarantine-v2"
        ):
            raise ValueError(
                "Guard-only mode requires the released v2 source reconstruction"
            )
    if phase == 2 and not args.bar_guard_only:
        progress = read_json(source / "progress.json")
        if (
            progress["state"] not in ("stopped", "completed", "budget_reached")
            or not (source / "STOP").exists()
        ):
            raise ValueError(
                "Phase-two source must be intentionally stopped and drained"
            )
        # PID creation time prevents a recycled PID from being mistaken for this producer.
        execution = read_json(source / "execution.json")
        if psutil.pid_exists(execution["pid"]):
            process = psutil.Process(execution["pid"])
            if abs(process.create_time() - execution["started"]) < 10:
                raise ValueError("Source producer still exists; wait for drain")
    output.mkdir(parents=True, exist_ok=True)
    # Single-writer lock is held throughout inventory, migration and final publication.
    import msvcrt

    owner = (output / "repair.lock").open("a+b")
    owner.write(b"0")
    owner.flush()
    owner.seek(0)
    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    binding = prepare(
        source,
        output,
        phase,
        args.source_revision,
        args.bar_guard_only,
        args.repaired_base,
    )
    ledger = sqlite3.connect(output / "repair.sqlite")
    ledger.executescript(
        "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA wal_autocheckpoint=4096;"
    )
    ledger.execute(
        "CREATE TABLE IF NOT EXISTS work(path TEXT PRIMARY KEY,done INTEGER DEFAULT 0,report TEXT,mappedkey TEXT,seal TEXT,bytes INTEGER)"
    )
    ledger.execute("CREATE UNIQUE INDEX IF NOT EXISTS mapped ON work(mappedkey)")
    began = time.time()
    inventory_path = output / "repair-inventory.json"
    inventory = (
        read_json(inventory_path)
        if inventory_path.exists() and not args.limit
        else None
    )
    if inventory:
        verify_seal(inventory, "hash")
        if (
            inventory["contract"] != read_json(output / "repair-contract.json")["hash"]
            or inventory["receipts"]
            != ledger.execute("SELECT count(*) FROM work").fetchone()[0]
        ):
            raise ValueError("Repair inventory no longer matches its frozen ledger")
    discovery = (
        ()
        if inventory
        else source.glob(
            "jobs/*/*/receipt.json" if phase == 1 else "streams/*/*/receipt.json"
        )
    )
    for index, path in enumerate(discovery):
        ledger.execute(
            "INSERT OR IGNORE INTO work(path) VALUES(?)",
            (str(path.relative_to(source)),),
        )
        if index % 1000 == 0:
            ledger.commit()
            write_json(
                output / "repair-progress.json",
                dict(state="inventory", updated=time.time(), discovered=index + 1),
            )
        if args.limit and index + 1 >= args.limit:
            break
    ledger.commit()
    total = ledger.execute("SELECT count(*) FROM work").fetchone()[0]
    if not args.limit and not inventory:
        inventory = dict(
            contract=read_json(output / "repair-contract.json")["hash"], receipts=total
        )
        inventory["hash"] = digest(inventory)
        write_json(inventory_path, inventory)
    completed = ledger.execute("SELECT count(*) FROM work WHERE done=1").fetchone()[0]
    counts = Counter(
        {
            name: int(total)
            for name, total in ledger.execute(
                "SELECT item.key,sum(item.value) FROM work,json_each(work.report,'$.counts') item WHERE done=1 GROUP BY item.key"
            )
        }
    )
    counts["changed_files"] = int(
        ledger.execute(
            "SELECT coalesce(sum(json_extract(report,'$.changed_files')),0) FROM work WHERE done=1"
        ).fetchone()[0]
    )
    active = {}
    pending = iter(
        row[0]
        for row in ledger.execute("SELECT path FROM work WHERE done=0 ORDER BY rowid")
    )
    failure = None
    campaign = args.training_campaign or source.parent / "overnight-v6-20261108"
    guard = TrainingGuard(campaign if campaign.exists() else None, args.workers, 4)
    last_progress = 0
    with ThreadPoolExecutor(args.workers, thread_name_prefix="quote-repair") as pool:
        while True:
            stopped = (output / "STOP").exists()
            admission = guard.refresh()
            while not stopped and not failure and len(active) < admission:
                relative = next(pending, None)
                if relative is None:
                    break
                if (
                    psutil.virtual_memory().available < 64 * 1024**3
                    or shutil.disk_usage(output).free < 150 * 1024**3
                ):
                    failure = (
                        "Memory/disk reserve reached; resume after capacity is restored"
                    )
                    break
                active[
                    pool.submit(
                        migrate_receipt,
                        source,
                        output,
                        Path(relative),
                        binding,
                        phase,
                        args.bar_guard_only,
                    )
                ] = relative
            if not active:
                break
            ready, _ = wait(active, timeout=3, return_when=FIRST_COMPLETED)
            for future in ready:
                relative = active.pop(future)
                try:
                    receipt = future.result()
                    report = receipt["quote_repair"]
                    counts.update(report["counts"])
                    counts["changed_files"] += report["changed_files"]
                    ledger.execute(
                        "UPDATE work SET done=1,report=?,mappedkey=?,seal=?,bytes=? WHERE path=?",
                        (
                            json.dumps(report),
                            (
                                f"{receipt['stream']}/{receipt['unit']:04d}"
                                if phase == 2
                                else relative
                            ),
                            receipt["hash" if phase == 2 else "receipt_hash"],
                            receipt["bytes"],
                            relative,
                        ),
                    )
                    completed += 1
                except Exception as error:
                    (output / "last-error.txt").write_text(traceback.format_exc())
                    failure = f"{relative}: {type(error).__name__}: {error}"
            ledger.commit()
            snapshot = dict(
                state="failed" if failure else ("draining" if stopped else "repairing"),
                updated=time.time(),
                phase=phase,
                completed=completed,
                total=total,
                active=len(active),
                counts=dict(counts),
                elapsed_seconds=time.time() - began,
                failure=failure,
                pilot=bool(args.limit),
                admission_lanes=admission,
                training_guard=guard.reason,
            )
            if failure or time.monotonic() - last_progress >= 3:
                write_json(output / "repair-progress.json", snapshot)
                last_progress = time.monotonic()
    state = (
        "failed"
        if failure
        else (
            "stopped"
            if (output / "STOP").exists()
            else ("pilot_complete" if args.limit else "payloads_complete")
        )
    )
    if state == "payloads_complete":
        # Sum durable reports across ALL attempts, not just this process's delta.
        counts = {
            name: int(total)
            for name, total in ledger.execute(
                "SELECT item.key,sum(item.value) FROM work,json_each(work.report,'$.counts') item WHERE done=1 GROUP BY item.key"
            )
        }
        counts["changed_files"] = int(
            ledger.execute(
                "SELECT coalesce(sum(json_extract(report,'$.changed_files')),0) FROM work WHERE done=1"
            ).fetchone()[0]
        )
        finalize(source, output, phase, binding, ledger, counts)
        state = "complete"
    write_json(
        output / "repair-progress.json",
        dict(
            state=state,
            updated=time.time(),
            completed=completed,
            total=total,
            counts=dict(counts),
            failure=failure,
            phase=phase,
            pilot=bool(args.limit),
        ),
    )
    ledger.close()
    for builder in BUILDERS:
        builder.close()
    if failure:
        raise RuntimeError(failure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
