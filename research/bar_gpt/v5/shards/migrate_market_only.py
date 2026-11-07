"""Receipt-preserving v5→v6 migration: drop halt columns, retain every sample.

No market events are queried or re-aggregated. Each source publication is hashed,
converted with NumPy, atomically published under the sealed v6 contract, then
read back and compared field-for-field to the source's first 18 market columns.
The old dataset remains immutable and stopped until the new version is verified.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import time

import numpy as np

from .balanced_build import main as prepare, Quotas
from .contract import SAMPLE_DTYPE as LEGACY_DTYPE
from .phase1_contract import SAMPLE_DTYPE, VERSION, digest, write_json, file_hash
from .storage import BAR_DTYPE, Budget, publish, verify_receipt


def migrate_unit(path, target, binding, budget, shard_bytes):
    """Convert one immutable publication and verify all retained field values."""
    original = verify_receipt(path.parent, hashes=True)
    existing = verify_receipt(target, hashes=True)
    if existing:
        if (
            existing.get("parent_receipt_hash") != original["receipt_hash"]
            or existing["binding"] != binding
        ):
            raise ValueError("Migration publication identity changed")
        return existing
    parts = []
    for item in original["files"]:
        old = np.load(path.parent / item["name"], mmap_mode="r", allow_pickle=False)
        if old.dtype != LEGACY_DTYPE:
            raise ValueError("Migration requires the archived 21-channel v5 dtype")
        new = np.empty(len(old), dtype=SAMPLE_DTYPE)
        new["values"] = old["values"][..., :18]
        new["valid"] = old["valid"][..., :18]
        for field in ("end_us", "timeframe_us", "split"):
            new[field] = old[field]
        if np.any(new["end_us"] <= 0) or np.any(np.diff(new["end_us"], axis=1) <= 0):
            raise ValueError(
                "Market-only phase-one records must contain 64 complete real bars"
            )
        old._mmap.close()
        parts.append(new)
    data = np.concatenate(parts) if parts else np.empty(0, dtype=SAMPLE_DTYPE)
    excluded = {
        "files",
        "samples",
        "candidate_samples",
        "excluded_no_price",
        "observed_bars",
        "padded_bars",
        "valid_bars_by_channel",
        "frames",
        "bytes",
        "receipt_hash",
        "binding",
        "identity",
    }
    metadata = {k: v for k, v in original.items() if k not in excluded}
    metadata.update(
        binding=binding,
        parent_binding=original["binding"],
        parent_receipt_hash=original["receipt_hash"],
        migration="drop-halt-columns-v1; retained market values and sample clocks unchanged",
        identity=digest(
            [
                binding,
                original["ticker"],
                original["unit"],
                int(data["timeframe_us"][0]),
                int(original["year"]),
            ]
        ),
    )
    result = publish(
        target, data, np.empty(0, dtype=BAR_DTYPE), metadata, budget, shard_bytes
    )
    if result is None:
        raise RuntimeError("Migration exceeded the configured byte budget")
    # Verify stored output independently of the assignment above. File boundaries
    # may change at the smaller record width; sample ordering must not change.
    saved = np.concatenate(
        [np.load(target / f["name"], allow_pickle=False) for f in result["files"]]
    )
    assert len(saved) == original["samples"]
    offset = 0
    for item in original["files"]:
        old = np.load(path.parent / item["name"], mmap_mode="r", allow_pickle=False)
        selected = saved[offset : offset + len(old)]
        np.testing.assert_array_equal(selected["values"], old["values"][..., :18])
        np.testing.assert_array_equal(selected["valid"], old["valid"][..., :18])
        for field in ("end_us", "timeframe_us", "split"):
            np.testing.assert_array_equal(selected[field], old[field])
        offset += len(old)
        old._mmap.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v5"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6"),
    )
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--target-bytes", type=int, default=10**12)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args(argv)
    state = json.loads((args.source / "progress.json").read_text())
    source_contract = json.loads((args.source / "contract.json").read_text())
    if source_contract["contract"]["version"] != "bargpt-ordinal-balanced-64-v5":
        raise ValueError("Migration requires the stopped balanced v5 dataset")
    if state["state"] not in (
        "stopped",
        "quotas_reached",
        "inventory_exhausted",
    ) or state.get("jobs_active", 0):
        raise ValueError("Stop and drain the source dataset before migration")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.output.resolve() == args.source.resolve():
        raise ValueError("Migration requires a distinct immutable dataset root")
    # The source-only ordinal plan is unchanged. Copy and verify its sealed files;
    # the v6 contract binds those same source ranges and a new market-only schema.
    shutil.copytree(args.source / "plan", args.output / "plan", dirs_exist_ok=True)
    prepare(
        [
            "--output",
            str(args.output),
            "--prepare-only",
            "--target-bytes",
            str(args.target_bytes),
            "--workers",
            "64",
            "--io-workers",
            "16",
            "--chunk-events",
            "500000",
            "--max-chunk-events",
            "500000",
            "--coarse-days",
            "20",
            "--source-revision",
            args.source_revision,
        ]
    )
    wrapper = json.loads((args.output / "contract.json").read_text())
    original_wrapper = json.loads((args.source / "contract.json").read_text())
    for key in ("dictionary_hash", "corporate_actions"):
        if (
            wrapper["contract"]["authority"][key]
            != original_wrapper["contract"]["authority"][key]
        ):
            raise ValueError(
                "Canonical dictionary or split authority changed; migration cannot mix sources"
            )
    import msvcrt

    owner = (args.output / "controller.lock").open("r+b")
    owner.seek(0)
    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
    binding = wrapper["binding"]
    budget = Budget(
        args.target_bytes,
        sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file()),
    )
    publications = list(args.source.glob("jobs/*/*/receipt.json"))
    paths = iter(publications)
    quotas = Quotas(args.target_bytes)
    counts = Counter()
    samples = completed = 0
    began = time.time()
    active = {}
    failures = []

    def snapshot(state="migrating"):
        write_json(
            args.output / "progress.json",
            dict(
                state=state,
                updated=time.time(),
                bytes=budget.used,
                target_bytes=args.target_bytes,
                samples=samples,
                frames=dict(counts),
                jobs_completed=completed,
                jobs_active=len(active),
                jobs_total=len(publications),
                elapsed_seconds=time.time() - began,
                bytes_per_second=budget.used / max(1, time.time() - began),
                quotas=quotas.snapshot(),
                workers=[],
                failed=failures,
                configuration=dict(
                    workers=args.workers,
                    io_workers=args.workers,
                    chunk_events=500000,
                    max_chunk_events=500000,
                    coarse_days=20,
                ),
            ),
        )

    with ThreadPoolExecutor(
        args.workers, thread_name_prefix="market-only-migration"
    ) as pool:

        def admit():
            if (args.output/'STOP').exists():
                return
            path = next(paths, None)
            if path is not None:
                target = args.output / path.parent.relative_to(args.source)
                active[
                    pool.submit(
                        migrate_unit, path, target, binding, budget, 32 * 1024**2
                    )
                ] = path

        for _ in range(args.workers):
            admit()
        while active:
            ready, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
            for future in ready:
                path = active.pop(future)
                try:
                    receipt = future.result()
                except Exception as error:
                    failures.append(dict(path=str(path), error=str(error)))
                    snapshot("failed")
                    raise
                completed += 1
                samples += receipt["samples"]
                counts.update(receipt["frames"])
                frame = next(f for f, n in receipt["frames"].items() if n)
                quotas.saved[frame][receipt["liquid"]] += receipt["samples"]
                admit()
            snapshot()
    if (args.output/'STOP').exists():
        snapshot('stopped')
        return 2
    # Continue exactly after committed source work. Drop only the three channels
    # in the tiny unadjusted coarse tail; halt-history caches are not copied.
    for path in (args.source / "checkpoints").glob("*.json"):
        if not re.fullmatch("[a-f0-9]{20}", path.stem):
            continue
        checkpoint = json.loads(path.read_text())
        checkpoint["binding"] = binding
        for tail in checkpoint.get("tail", {}).values():
            tail[0] = [row[:18] for row in tail[0]]
            tail[1] = [row[:18] for row in tail[1]]
        target = args.output / "checkpoints" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, checkpoint)
    expected = json.loads((args.source / "result.json").read_text())["samples"]
    if samples != expected:
        raise ValueError("Migrated sample count differs from the drained source")
    result = dict(
        passed=True,
        source=str(args.source),
        dataset=str(args.output),
        samples=samples,
        receipts=completed,
        frames=dict(counts),
        market_channels=18,
        model_channels=27,
        saved_payload_bytes=samples * (LEGACY_DTYPE.itemsize - SAMPLE_DTYPE.itemsize),
        verification="Every retained value, mask, timestamp, timeframe and split compared to source",
        source_revision=args.source_revision,
        binding=binding,
    )
    write_json(args.output / "migration-audit.json", result)
    snapshot("migration_complete")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
