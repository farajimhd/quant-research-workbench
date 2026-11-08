"""Receipt-bound mmap loading, chronological split, and bounded CPU preparation workers."""

from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, IterableDataset

from .pretraining_v1 import ActivityStats, fit_activity_stats, prepare_batch
from .cohort_v1 import members as cohort_members
from .shards.phase1_contract import (
    CHANNELS,
    SAMPLE_DTYPE,
    VERSION,
    digest,
    file_hash,
    write_json,
)


def dataset_identity(dataset: Path) -> dict:
    """Validate a production v6 contract; contract-free inventories are explicit test fixtures."""
    path = dataset / "contract.json"
    seal, components = cohort_members(dataset, require_all=True)
    if not path.exists() and seal is not None:
        path = components[0][1] / "contract.json"
    if not path.exists():
        return {}
    wrapper = json.loads(path.read_text())
    if wrapper["binding"] != digest(wrapper["contract"]):
        raise ValueError("Dataset contract seal changed.")
    if wrapper["contract"]["version"] != VERSION:
        raise ValueError(
            "Training requires v6 market-only shards and a fresh split/calibration."
        )
    identity = dict(
        dataset_binding=wrapper["binding"],
        dataset_version=VERSION,
        market_channels=list(CHANNELS),
    )
    if seal is not None:
        identities = []
        for name, root in components:
            member = json.loads((root / "contract.json").read_text())
            if (
                member["binding"] != digest(member["contract"])
                or member["contract"]["version"] != VERSION
            ):
                raise ValueError(f"Cohort member {name} has an invalid v6 contract")
            if name != "base" and (
                member["contract"].get("parent_binding") != wrapper["binding"]
                or member["contract"].get("authority")
                != wrapper["contract"].get("authority")
            ):
                raise ValueError(
                    f"Cohort member {name} does not share the base's canonical authority"
                )
            identities.append(
                dict(name=name, path=str(root), binding=member["binding"])
            )
        identity.update(cohort_hash=seal, dataset_members=identities)
    return identity


def validate_complete_market(rows):
    """Reject malformed v6 [N,64,18] records; no silent exclusion of authoritative samples."""
    if (
        rows.dtype != SAMPLE_DTYPE
        or rows.ndim != 1
        or not rows["valid"][..., :6].all()
        or np.any(rows["values"][..., :6] <= 0)
        or not np.isfinite(rows["values"]).all()
        or not np.isin(rows["valid"], (0, 1)).all()
        or np.any(rows["end_us"] <= 0)
        or np.any(np.diff(rows["end_us"], axis=1) <= 0)
    ):
        raise ValueError(
            "Invalid v6 sample: require 64 ordered real bars and complete positive trade fields."
        )


def read_receipt(path: Path, expected_binding=None) -> list[dict]:
    """Validate a publication seal and NPY headers; defer payload hashing until use."""
    receipt = json.loads(path.read_text())
    if (
        digest({k: v for k, v in receipt.items() if k != "receipt_hash"})
        != receipt["receipt_hash"]
    ):
        raise ValueError(f"Receipt digest mismatch: {path}")
    if expected_binding is not None and receipt.get("binding") != expected_binding:
        raise ValueError(f"Receipt belongs to a different dataset contract: {path}")
    entries = []
    for item in receipt["files"]:
        if not item["name"].startswith("samples-"):
            continue  # coarse.npy contains intermediate bars, never training samples.
        target = path.parent / item["name"]
        if target.parent != path.parent or target.stat().st_size != item["bytes"]:
            raise ValueError(f"Changed shard size/path: {target}")
        data = np.load(target, mmap_mode="r", allow_pickle=False)
        if data.dtype != SAMPLE_DTYPE or data.ndim != 1:
            raise ValueError(f"Wrong sample contract: {target}")
        entries.append(
            dict(
                path=str(target.resolve()),
                rows=len(data),
                start=0,
                stop=len(data),
                day=receipt["day"],
                sha256=item["sha256"],
                bytes=item["bytes"],
                first_start_us=item.get("first_start_us"),
                last_end_us=item.get("last_end_us"),
                receipt_hash=receipt["receipt_hash"],
                frames=receipt["frames"],
            )
        )
    if sum(item["rows"] for item in entries) != receipt["samples"]:
        raise ValueError(f"Receipt sample count mismatch: {path}")
    return entries


def build_split(dataset: Path, output: Path, progress=None) -> dict:
    """Freeze sealed inventory, target earliest 80% by sample count, purge crossing windows.

    Whole trading dates stay together. The split is nearest to 80/20, not a random
    split of overlapping contexts. 04:00 UTC lies between US extended sessions in
    both DST regimes. Coarse windows must lie wholly on one side; their starts use
    conservative nominal duration (24h for daily). Existing shard split flags are
    deliberately superseded in this separate manifest, never overwritten.
    """
    identity = dataset_identity(
        dataset
    )  # Pin production schema/authority before indexing any publication.
    components = identity.get(
        "dataset_members",
        [dict(path=str(dataset), binding=identity.get("dataset_binding"))],
    )
    paths = [
        (path, component["binding"])
        for component in components
        for path in sorted((Path(component["path"]) / "jobs").rglob("receipt.json"))
    ]
    if not paths:
        raise ValueError("No published sample receipts found.")
    entries = []
    from .receipt_index_v1 import index_receipt

    reader = (
        index_receipt if identity.get("dataset_version") == VERSION else read_receipt
    )
    with ThreadPoolExecutor(max_workers=32) as pool:
        for begin in range(0, len(paths), 1024):
            for local, result in enumerate(
                pool.map(
                    lambda item: reader(item[0], item[1]), paths[begin : begin + 1024]
                )
            ):
                index = begin + local
                entries.extend(result)
                if progress and index % 500 == 0:
                    progress(stage="indexing receipts", done=index, total=len(paths))
    day_counts = defaultdict(int)
    for item in entries:
        if item["day"] != "coarse":
            day_counts[item["day"]] += item["rows"]
    days = sorted(day_counts)
    if len(days) < 2:
        raise ValueError("Chronological validation needs at least two trading dates.")
    cumulative = np.cumsum([day_counts[day] for day in days])
    boundary = 1 + int(np.argmin(np.abs(cumulative[:-1] - 0.8 * cumulative[-1])))
    cutoff_day = days[boundary]
    cutoff = int(
        datetime.fromisoformat(cutoff_day + "T04:00:00+00:00").timestamp() * 1e6
    )
    splits, purged = {"train": [], "validation": []}, 0
    for item in entries:
        if item["last_end_us"] is not None and item["last_end_us"] < cutoff:
            splits["train"].append(item)
            continue
        if item["first_start_us"] is not None and item["first_start_us"] >= cutoff:
            splits["validation"].append(item)
            continue
        if item["day"] != "coarse" and item["first_start_us"] is None:
            splits["train" if item["day"] < cutoff_day else "validation"].append(item)
            continue
        data = np.load(item["path"], mmap_mode="r", allow_pickle=False)
        train = (
            data["end_us"].max(axis=1) < cutoff
        )  # Ignore zero padding; include the entire real lookback.
        validation = data["end_us"][:, 0] - data["timeframe_us"] >= cutoff
        purged += int((~train & ~validation).sum())
        for name, mask in (("train", train), ("validation", validation)):
            # Preserve every selected row as contiguous ranges, including fragmented coarse segments.
            edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(np.int8)))
            for start, stop in edges.reshape(-1, 2):
                splits[name].append(dict(item, start=int(start), stop=int(stop)))
    counts = {
        name: sum(item["stop"] - item["start"] for item in rows)
        for name, rows in splits.items()
    }
    if not all(counts.values()):
        raise ValueError(
            "The purged chronological split needs nonempty train and validation sets."
        )
    manifest = dict(
        version="chronological-80-20-v1",
        dataset=str(dataset.resolve()),
        cutoff_day=cutoff_day,
        cutoff_us=cutoff,
        counts=counts,
        purged_crossing=purged,
        train_fraction=counts["train"] / sum(counts.values()),
        splits=splits,
        inventory_samples=sum(item["rows"] for item in entries),
        integrity=(
            "receipt digests and fixed-schema byte counts verified; actual headers and payload SHA256 verified on loader access"
            if reader is index_receipt
            else "receipt digests and headers verified; payload SHA256 verified on first access per worker"
        ),
    )
    manifest.update(
        identity
    )  # Resume binds the frozen inventory to its exact v6 source authority.
    manifest["manifest_hash"] = digest(manifest)
    write_json(output, manifest)
    return manifest


def load_split(path: Path) -> dict:
    """Read an immutable split and verify its complete inventory digest."""
    manifest = json.loads(path.read_text())
    if (
        digest({k: v for k, v in manifest.items() if k != "manifest_hash"})
        != manifest["manifest_hash"]
    ):
        raise ValueError("Split manifest was modified.")
    dataset = Path(manifest["dataset"])
    if (
        manifest.get("dataset_binding")
        and not (dataset / "contract.json").is_file()
        and not (dataset / "cohort.json").is_file()
    ):
        raise ValueError("The split's sealed dataset contract is missing.")
    for key, value in dataset_identity(dataset).items():
        if manifest.get(key) != value:
            raise ValueError(
                "Split belongs to a different dataset contract; rebuild split and calibration."
            )
    return manifest


def calibrate(
    manifest: dict, maximum: int = 8192, seed: int = 17
) -> tuple[ActivityStats, dict]:
    """Bounded stratified training sample; return stats and selection provenance."""
    entries = manifest["splits"]["train"]
    lengths = np.array(
        [item["stop"] - item["start"] for item in entries], dtype=np.int64
    )
    rng = np.random.default_rng(seed)
    # Bound calibration I/O to 64 sealed files rather than randomly touching the 1 TB corpus.
    owners = rng.choice(
        len(entries), min(64, len(entries)), replace=False, p=lengths / lengths.sum()
    )
    values, masks, selection = [], [], []
    for owner in owners:
        item = entries[int(owner)]
        if file_hash(item["path"]) != item["sha256"]:
            raise ValueError(f"Changed calibration payload: {item['path']}")
        array = np.load(item["path"], mmap_mode="r", allow_pickle=False)
        candidate = array[item["start"] : item["stop"]]
        if manifest.get("dataset_version") == VERSION:
            validate_complete_market(
                candidate
            )  # Calibration must not hide malformed production samples.
        eligible = (
            np.flatnonzero(candidate["valid"][..., [3, 9, 15]].any(axis=(1, 2)))
            + item["start"]
        )
        count = min(math.ceil(maximum / len(owners)), len(eligible))
        if count == 0:
            selection.append(
                dict(path=item["path"], rows=[], reason="no_observed_closing_price")
            )
            continue
        local = rng.choice(eligible, count, replace=False)
        rows = array[local]
        values.append(rows["values"].copy())
        masks.append(rows["valid"].astype(bool))
        selection.append(dict(path=item["path"], rows=local.tolist()))
    if not values:
        raise ValueError(
            "Calibration sample contains no eligible training observations."
        )
    stats = fit_activity_stats(
        torch.from_numpy(np.concatenate(values)),
        torch.from_numpy(np.concatenate(masks)),
    )
    return stats, dict(
        seed=seed,
        samples=sum(len(v) for v in values),
        files=len(owners),
        method="64 size-weighted training ranges, uniform eligible rows within each; bounded calibration sample",
        split_hash=manifest["manifest_hash"],
        selection_hash=digest(selection),
        median=stats.median.tolist(),
        scale=stats.scale.tolist(),
    )


class ShardBatches(Dataset):
    """Map batch number to compact tensors; workers mmap, pack, scale, and mask concurrently.

    Shuffle shard ranges each epoch, then shuffle rows inside each batch. No global
    billion-row permutation or materialized redundant windows. Final partial batch
    is retained. Each worker has at most four open mmap files and verifies each new
    payload hash once. Seed depends on epoch and batch index, not scheduling order.
    """

    def __init__(
        self,
        manifest: dict,
        split: str,
        stats: ActivityStats,
        batch_size: int,
        seed: int = 0,
        epoch: int = 0,
        start_batch: int = 0,
        telemetry: str | None = None,
    ):
        if batch_size < 1 or start_batch < 0:
            raise ValueError(
                "Batch size must be positive and resume cursor nonnegative."
            )
        self.entries = list(manifest["splits"][split])
        self.strict_market = manifest.get("dataset_version") == VERSION
        if split == "train":
            np.random.default_rng(seed + epoch).shuffle(self.entries)
        self.cumulative = np.cumsum(
            [item["stop"] - item["start"] for item in self.entries]
        )
        self.stats, self.batch_size, self.seed, self.epoch = (
            stats,
            batch_size,
            seed,
            epoch,
        )
        self.start_batch = start_batch
        self.telemetry, self.last_report, self.completed = telemetry, 0.0, 0
        self.cache, self.verified = OrderedDict(), set()
        self.total = math.ceil(int(self.cumulative[-1]) / batch_size)
        if start_batch > self.total:
            raise ValueError("Resume cursor exceeds dataset length.")

    def __len__(self):
        return self.total - self.start_batch

    def __getitem__(self, index):
        began = time.perf_counter()
        if index < 0 or index >= len(self):
            raise IndexError(index)
        index += self.start_batch
        first, last = index * self.batch_size, min(
            (index + 1) * self.batch_size, int(self.cumulative[-1])
        )
        pieces = []
        while first < last:
            owner = int(np.searchsorted(self.cumulative, first, side="right"))
            item = self.entries[owner]
            path = item["path"]
            if path not in self.cache:
                if path not in self.verified:
                    if (
                        Path(path).stat().st_size != item["bytes"]
                        or file_hash(path) != item["sha256"]
                    ):
                        raise ValueError(f"Changed sealed payload: {path}")
                    self.verified.add(path)
                self.cache[path] = np.load(path, mmap_mode="r", allow_pickle=False)
                if len(self.cache) > 4:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(path)
            base = int(self.cumulative[owner - 1]) if owner else 0
            stop = min(last, int(self.cumulative[owner]))
            pieces.append(
                self.cache[path][
                    item["start"] + first - base : item["start"] + stop - base
                ]
            )
            first = stop
        rows = np.concatenate(
            pieces
        )  # One bounded batch copy, not a redundant context materialization.
        if self.strict_market:
            validate_complete_market(rows)
        rng = np.random.default_rng(self.seed + self.epoch * 1_000_003 + index)
        rows = rows[rng.permutation(len(rows))]
        generator = torch.Generator().manual_seed(
            self.seed + self.epoch * 1_000_003 + index
        )
        batch = prepare_batch(
            torch.from_numpy(rows["values"].copy()),
            torch.from_numpy(rows["valid"].astype(bool)),
            torch.from_numpy(rows["end_us"].copy()),
            torch.from_numpy(rows["timeframe_us"].copy()),
            self.stats,
            generator,
        )
        self.completed += 1
        if self.telemetry and time.perf_counter() - self.last_report > 1:
            worker = torch.utils.data.get_worker_info()
            identity = worker.id if worker else 0
            write_json(
                Path(self.telemetry) / f"worker-{identity:02d}.json",
                dict(
                    id=identity,
                    stage="prepared / prefetching",
                    batches=self.completed,
                    prepare_ms=(time.perf_counter() - began) * 1000,
                    updated=time.time(),
                ),
            )
            self.last_report = time.perf_counter()
        return batch


def worker_init(worker_id):
    """Prevent CPU oversubscription: each process performs vectorized work on one thread."""
    torch.set_num_threads(1)


class EligibleStream(IterableDataset):
    """Pack full eligible batches from disjoint worker shard lanes, without scanning 1 TB upfront.

    Exclusions are explicit: no observed trade/bid/ask closing price. Every other
    raw validation failure remains fatal. At epoch end each worker emits its tail.
    Resume deterministically replays the worker's prefix reads without training;
    it never repeats optimizer updates. This avoids a huge persistent row index.
    Worker count is part of the resume contract because it defines stream ordering.
    """

    def __init__(
        self,
        manifest,
        split,
        stats,
        batch_size,
        seed=0,
        epoch=0,
        start_batch=0,
        telemetry=None,
    ):
        self.entries = list(manifest["splits"][split])
        self.strict_market = manifest.get("dataset_version") == VERSION
        if split == "train":
            np.random.default_rng(seed + epoch).shuffle(self.entries)
        self.stats, self.batch_size, self.seed, self.epoch = (
            stats,
            batch_size,
            seed,
            epoch,
        )
        self.start_batch, self.telemetry = start_batch, telemetry
        self.replay_cursor = start_batch  # Controller replays the ordered loader prefix, including uneven lane tails.
        self.counters = torch.zeros(
            (32, 3), dtype=torch.int64
        ).share_memory_()  # One writer per worker row.
        self.total = math.ceil(
            sum(item["stop"] - item["start"] for item in self.entries) / batch_size
        )

    def __len__(self):
        return max(
            0, self.total - self.start_batch
        )  # Raw upper bound; not an eligible completion claim.

    def __iter__(self):
        worker = torch.utils.data.get_worker_info()
        physical, workers = (worker.id, worker.num_workers) if worker else (0, 1)
        lane, skip = physical, 0
        completed = scanned = excluded = pending_count = 0
        pieces = []
        began = time.perf_counter()
        verified = set()
        last_report = 0.0

        def publish(stage):
            nonlocal last_report
            self.counters[physical] = torch.tensor([scanned, excluded, completed])
            if stage != "lane complete" and time.perf_counter() - last_report < 1:
                return  # High-rate shard reads must not turn telemetry into synchronous disk work.
            if self.telemetry:
                write_json(
                    Path(self.telemetry) / f"worker-{physical:02d}.json",
                    dict(
                        id=physical,
                        lane=lane,
                        stage=stage,
                        batches=completed,
                        scanned_candidates=scanned,
                        excluded_no_price=excluded,
                        prepare_ms=(time.perf_counter() - began) * 1000,
                        updated=time.time(),
                    ),
                )
                last_report = time.perf_counter()

        def pack(rows):
            # Lane-local sequence identifies the batch independently of scheduling/prefetch timing.
            rng = self.seed + self.epoch * 1_000_003 + completed * workers + lane
            rows = rows[np.random.default_rng(rng).permutation(len(rows))]
            return prepare_batch(
                torch.from_numpy(rows["values"].copy()),
                torch.from_numpy(rows["valid"].astype(bool)),
                torch.from_numpy(rows["end_us"].copy()),
                torch.from_numpy(rows["timeframe_us"].copy()),
                self.stats,
                torch.Generator().manual_seed(rng),
            )

        for item in self.entries[lane::workers]:
            path = item["path"]
            publish("reading / verifying")
            if path not in verified:
                if (
                    Path(path).stat().st_size != item["bytes"]
                    or file_hash(path) != item["sha256"]
                ):
                    raise ValueError(f"Changed sealed payload: {path}")
                verified.add(path)
            data = np.load(path, mmap_mode="r", allow_pickle=False)[
                item["start"] : item["stop"]
            ]
            if self.strict_market:
                validate_complete_market(
                    data
                )  # Invalid v6 bars stop the run instead of becoming exclusions.
            observed = data["valid"][..., [3, 9, 15]].any(axis=(1, 2))
            scanned += len(data)
            excluded += int((~observed).sum())
            selected = data[observed]  # Compact eligible records once per small shard.
            offset = 0
            while offset < len(selected):
                take = min(self.batch_size - pending_count, len(selected) - offset)
                pieces.append(selected[offset : offset + take])
                pending_count += take
                offset += take
                if pending_count == self.batch_size:
                    rows = np.concatenate(pieces)
                    pieces, pending_count = [], 0
                    if completed >= skip:
                        batch = pack(rows)
                        completed += 1
                        publish("prepared / prefetching")
                        yield batch
                        began = time.perf_counter()
                    else:
                        completed += 1  # Resume replay: consume source prefix without optimizer or augmentation.
        if pending_count:
            if completed >= skip:
                batch = pack(np.concatenate(pieces))
                completed += 1
                publish("final partial batch")
                yield batch
        publish("lane complete")


def make_loader(
    dataset: ShardBatches, workers: int = 8, prefetch: int = 2
) -> DataLoader:
    """Bound ready batches to workers*prefetch and pin only prepared CPU tensors."""
    if not 0 <= workers <= 32 or not 1 <= prefetch <= 4:
        raise ValueError("Workers must be 0..32; prefetch must be 1..4.")
    if isinstance(dataset, EligibleStream):
        lanes = max(1, workers)
        dataset.total = sum(
            math.ceil(
                sum(
                    item["stop"] - item["start"]
                    for item in dataset.entries[lane::lanes]
                )
                / dataset.batch_size
            )
            for lane in range(lanes)
        )
        # A per-lane upper bound retains EVERY worker's partial tail, not just one global tail.
    options = dict(
        batch_size=None,
        num_workers=workers,
        pin_memory=torch.cuda.is_available(),
        worker_init_fn=worker_init,
        generator=torch.Generator().manual_seed(dataset.seed + dataset.epoch),
    )
    if workers:
        options.update(
            prefetch_factor=prefetch,
            persistent_workers=True,
            multiprocessing_context="spawn",
        )
    return DataLoader(dataset, **options)


def close_loader(loader):
    """Stop persistent workers promptly, including interrupted profiling trials."""
    iterator = getattr(loader, "_iterator", None)
    if iterator is not None:
        iterator._shutdown_workers()  # PyTorch has no public close method for persistent workers.
