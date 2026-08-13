from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from research.bar_gpt.v2.config import DataConfig
from research.bar_gpt.v2.model_discovery import (
    DISCOVERY_CONTRACT_VERSION,
    _held_out_panel,
    _panel_summary,
    discovery_data_config,
    discovery_shard_compatibility_hash,
    enumerate_block_refs,
    load_discovery_manifest,
)
from research.bar_gpt.v2.offline_shards import (
    OfflineBlockRef,
    OfflineShardUnit,
    discover_offline_units,
    verify_shard_catalog_lock,
)


FULL_CHUNK_CONTRACT_VERSION = 2
FULL_CHUNK_MANIFEST_NAME = "full_catalog_chunks_v2.json"
FULL_CHUNK_TARGET_ORIGINS = 30_000_000
FULL_CHUNK_STOPPING_VALIDATION_ORIGINS = 1_000_000
FULL_CHUNK_VALIDATION_ORIGINS = 5_000_000
FULL_CHUNK_LOCKED_TEST_ORIGINS = 5_000_000


def _canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ref_identity(ref: OfflineBlockRef) -> str:
    return (
        f"{ref.unit_key}|{ref.session_index}|{ref.block_index}|"
        f"{ref.ticker}|{ref.local_date}|{ref.block_offset}"
    )


def _sample_monitor_panel(
    refs: Sequence[OfflineBlockRef],
    *,
    target_origins: int,
    seed: int,
    epoch: int,
    chunk_index: int,
) -> tuple[OfflineBlockRef, ...]:
    """Return a deterministic ticker-balanced random panel for one chunk."""
    if target_origins <= 0:
        raise ValueError("monitor target origins must be positive")
    by_ticker: dict[str, list[OfflineBlockRef]] = {}
    for ref in refs:
        by_ticker.setdefault(ref.ticker, []).append(ref)
    return _sample_grouped_monitor_panel(
        {
            ticker: tuple(sorted(values, key=_ref_identity))
            for ticker, values in by_ticker.items()
        },
        target_origins=target_origins,
        seed=seed,
        epoch=epoch,
        chunk_index=chunk_index,
    )


def _sample_grouped_monitor_panel(
    by_ticker: dict[str, tuple[OfflineBlockRef, ...]],
    *,
    target_origins: int,
    seed: int,
    epoch: int,
    chunk_index: int,
) -> tuple[OfflineBlockRef, ...]:
    if not by_ticker:
        raise RuntimeError("full-training monitor reservoir is empty")
    per_ticker = max(1, math.ceil(target_origins / len(by_ticker)))
    selected: list[OfflineBlockRef] = []
    for ticker in sorted(by_ticker):
        label = f"monitor|{seed}|{epoch}|{chunk_index}|{ticker}"
        ordered = by_ticker[ticker]
        digest = hashlib.sha256(label.encode("utf-8")).digest()
        offset = int.from_bytes(digest[:8], "big") % len(ordered)
        stride = max(1, int.from_bytes(digest[8:16], "big") % len(ordered))
        while math.gcd(stride, len(ordered)) != 1:
            stride = stride % len(ordered) + 1
        ticker_origins = 0
        for position in range(len(ordered)):
            ref = ordered[(offset + position * stride) % len(ordered)]
            selected.append(ref)
            ticker_origins += int(ref.origins)
            if ticker_origins >= per_ticker:
                break
    selected.sort(
        key=lambda ref: hashlib.sha256(
            (
                f"chunk-validation-order|{seed}|{epoch}|{chunk_index}|"
                f"{_ref_identity(ref)}"
            ).encode("utf-8")
        ).digest()
    )
    origins = sum(int(ref.origins) for ref in selected)
    if origins < target_origins:
        raise RuntimeError(
            f"chunk validation panel has only {origins:,} origins; "
            f"{target_origins:,} required"
        )
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class ChunkEvaluationPlan:
    index: int
    target_blocks: int
    approximate_target_origins: int
    training_ref_indices: tuple[int, ...]
    validation_origins: int
    validation_hash: str
    validation_refs: tuple[OfflineBlockRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "target_blocks": self.target_blocks,
            "approximate_target_origins": self.approximate_target_origins,
            "training_ref_indices": list(self.training_ref_indices),
            "validation_origins": self.validation_origins,
            "validation_hash": self.validation_hash,
            "validation_refs": [asdict(ref) for ref in self.validation_refs],
        }


@dataclass(frozen=True, slots=True)
class EpochChunkPlan:
    contract_version: int
    epoch: int
    shuffle_seed: int
    training_blocks: int
    training_origins: int
    target_chunk_origins: int
    target_validation_origins: int
    chunks: tuple[ChunkEvaluationPlan, ...]
    plan_hash: str

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "epoch": self.epoch,
            "shuffle_seed": self.shuffle_seed,
            "training_blocks": self.training_blocks,
            "training_origins": self.training_origins,
            "target_chunk_origins": self.target_chunk_origins,
            "target_validation_origins": self.target_validation_origins,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "plan_hash": self.plan_hash,
        }


def build_epoch_chunk_plan(
    *,
    epoch: int,
    seed: int,
    training_blocks: int,
    training_origins: int,
    training_refs: Sequence[OfflineBlockRef],
    target_chunk_origins: int,
    validation_origins: int,
    monitor_pool: Sequence[OfflineBlockRef],
) -> EpochChunkPlan:
    """Plan block-aligned chunks and their fixed held-out validation panels.

    Training membership is an exact shuffled partition of stable catalog-ref
    indices. The plan therefore supports replaying one chunk without copying
    full block dictionaries or reading shard tensors during planning.
    """
    if epoch < 0:
        raise ValueError("epoch must be non-negative")
    if training_blocks <= 0 or training_origins <= 0:
        raise ValueError("training coverage must be positive")
    if len(training_refs) != training_blocks:
        raise ValueError("training reference count does not match training_blocks")
    if sum(int(ref.origins) for ref in training_refs) != training_origins:
        raise ValueError("training reference origins do not match training_origins")
    if target_chunk_origins <= 0 or validation_origins <= 0:
        raise ValueError("chunk and validation origin targets must be positive")
    chunk_count = max(1, math.ceil(training_origins / target_chunk_origins))
    base_blocks, extra = divmod(training_blocks, chunk_count)
    if base_blocks <= 0:
        raise RuntimeError("chunk target creates more chunks than training blocks")
    monitor_groups: dict[str, list[OfflineBlockRef]] = {}
    for ref in monitor_pool:
        monitor_groups.setdefault(ref.ticker, []).append(ref)
    grouped_monitor_pool = {
        ticker: tuple(sorted(values, key=_ref_identity))
        for ticker, values in monitor_groups.items()
    }
    shuffled_indices = list(range(training_blocks))
    random.Random(seed + epoch * 1_000_003).shuffle(shuffled_indices)
    chunks: list[ChunkEvaluationPlan] = []
    cursor = 0
    for chunk_index in range(chunk_count):
        target_blocks = base_blocks + int(chunk_index < extra)
        training_ref_indices = tuple(
            shuffled_indices[cursor:cursor + target_blocks]
        )
        cursor += target_blocks
        chunk_origins = sum(
            int(training_refs[index].origins) for index in training_ref_indices
        )
        panel = _sample_grouped_monitor_panel(
            grouped_monitor_pool,
            target_origins=validation_origins,
            seed=seed,
            epoch=epoch,
            chunk_index=chunk_index,
        )
        panel_rows = [asdict(ref) for ref in panel]
        chunks.append(
            ChunkEvaluationPlan(
                index=chunk_index,
                target_blocks=target_blocks,
                approximate_target_origins=chunk_origins,
                training_ref_indices=training_ref_indices,
                validation_origins=sum(int(ref.origins) for ref in panel),
                validation_hash=hashlib.sha256(
                    json.dumps(
                        panel_rows, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest(),
                validation_refs=panel,
            )
        )
    if cursor != training_blocks or len(set(shuffled_indices)) != training_blocks:
        raise RuntimeError("epoch chunk plan did not partition every training block exactly once")
    validation_hashes = [chunk.validation_hash for chunk in chunks]
    if len(validation_hashes) != len(set(validation_hashes)):
        raise RuntimeError(
            "validation reservoir cannot provide a distinct deterministic panel for every chunk"
        )
    unsigned = {
        "contract_version": FULL_CHUNK_CONTRACT_VERSION,
        "epoch": int(epoch),
        "shuffle_seed": int(seed + epoch * 1_000_003),
        "training_blocks": int(training_blocks),
        "training_origins": int(training_origins),
        "target_chunk_origins": int(target_chunk_origins),
        "target_validation_origins": int(validation_origins),
        "chunks": [chunk.to_dict() for chunk in chunks],
    }
    return EpochChunkPlan(
        contract_version=FULL_CHUNK_CONTRACT_VERSION,
        epoch=int(epoch),
        shuffle_seed=int(seed + epoch * 1_000_003),
        training_blocks=int(training_blocks),
        training_origins=int(training_origins),
        target_chunk_origins=int(target_chunk_origins),
        target_validation_origins=int(validation_origins),
        chunks=tuple(chunks),
        plan_hash=_canonical_hash(unsigned),
    )


def write_epoch_chunk_plan(plan: EpochChunkPlan, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    os.replace(temporary, output_path)


def load_epoch_chunk_plan(output_path: Path) -> EpochChunkPlan:
    value = json.loads(output_path.read_text(encoding="utf-8"))
    stored_hash = str(value.pop("plan_hash", ""))
    if int(value.get("contract_version", -1)) != FULL_CHUNK_CONTRACT_VERSION:
        raise RuntimeError("unsupported full-training epoch-plan contract")
    if _canonical_hash(value) != stored_hash:
        raise RuntimeError("full-training epoch-plan hash mismatch")
    chunks = tuple(
        ChunkEvaluationPlan(
            index=int(row["index"]),
            target_blocks=int(row["target_blocks"]),
            approximate_target_origins=int(row["approximate_target_origins"]),
            training_ref_indices=tuple(
                int(index) for index in row["training_ref_indices"]
            ),
            validation_origins=int(row["validation_origins"]),
            validation_hash=str(row["validation_hash"]),
            validation_refs=tuple(
                OfflineBlockRef(**ref) for ref in row["validation_refs"]
            ),
        )
        for row in value["chunks"]
    )
    return EpochChunkPlan(
        contract_version=int(value["contract_version"]),
        epoch=int(value["epoch"]),
        shuffle_seed=int(value["shuffle_seed"]),
        training_blocks=int(value["training_blocks"]),
        training_origins=int(value["training_origins"]),
        target_chunk_origins=int(value["target_chunk_origins"]),
        target_validation_origins=int(value["target_validation_origins"]),
        chunks=chunks,
        plan_hash=stored_hash,
    )


def build_full_chunk_manifest(
    *,
    shard_root: Path,
    output_path: Path,
    seed: int,
    monitor_origins: int = FULL_CHUNK_STOPPING_VALIDATION_ORIGINS,
    validation_origins: int = FULL_CHUNK_VALIDATION_ORIGINS,
    locked_test_origins: int = FULL_CHUNK_LOCKED_TEST_ORIGINS,
) -> dict[str, Any]:
    """Freeze complete training coverage and disjoint held-out authorities."""
    verify_shard_catalog_lock(shard_root)
    config = discovery_data_config(shard_root)
    tickers = tuple(config.tickers)
    training_units = discover_offline_units(
        shard_root,
        config,
        tickers=tickers,
        start_date="2019-01-01",
        end_date="2026-01-01",
    )
    held_out_units = discover_offline_units(
        shard_root,
        config,
        tickers=tickers,
        start_date="2026-01-01",
        end_date="2026-08-01",
    )
    index_root = output_path.parent / "full_catalog_index_v1"
    training_refs = enumerate_block_refs(
        training_units,
        label="full-training",
        cache_path=index_root / "training.jsonl",
    )
    held_out_refs = enumerate_block_refs(
        held_out_units,
        label="full-held-out",
        cache_path=index_root / "held_out.jsonl",
    )
    used_dates: set[tuple[str, str]] = set()
    validation = _held_out_panel(
        held_out_refs,
        target_origins=validation_origins,
        seed=seed,
        label="full_validation",
        used_dates=used_dates,
        reserve_dates_per_ticker=1,
        require_every_ticker=True,
    )
    locked_test = _held_out_panel(
        held_out_refs,
        target_origins=locked_test_origins,
        seed=seed,
        label="full_locked_test",
        used_dates=used_dates,
        require_every_ticker=False,
    )
    monitor_pool = tuple(
        ref for ref in held_out_refs if (ref.ticker, ref.local_date) not in used_dates
    )
    monitor = _sample_monitor_panel(
        monitor_pool,
        target_origins=monitor_origins,
        seed=seed,
        epoch=0,
        chunk_index=0,
    )
    epoch_train = _sample_monitor_panel(
        training_refs,
        target_origins=monitor_origins,
        seed=seed + 13_337,
        epoch=0,
        chunk_index=0,
    )
    training_summary = _panel_summary(training_refs)
    training_catalog_hash = hashlib.sha256()
    for ref in training_refs:
        training_catalog_hash.update(_ref_identity(ref).encode("utf-8"))
        training_catalog_hash.update(b"\n")
    panels = {
        # The complete training population is the certified 2019-2025 unit
        # stream itself. Do not serialize its roughly one million block refs
        # into the manifest and then duplicate them in every loader process.
        # The summary and catalog hash below bind the implicit population.
        "epoch_train": [asdict(ref) for ref in epoch_train],
        "monitor": [asdict(ref) for ref in monitor],
        "monitor_pool": [asdict(ref) for ref in monitor_pool],
        "validation": [asdict(ref) for ref in validation],
        "locked_test": [asdict(ref) for ref in locked_test],
    }
    value: dict[str, Any] = {
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "seed": int(seed),
        "shard_root": str(shard_root),
        "shard_config_hash": discovery_shard_compatibility_hash(config),
        "cohorts": {
            "training_tickers": sorted(tickers),
            "evaluation_tickers": sorted(tickers),
        },
        "ranges": {
            "train": ["2019-01-01", "2026-01-01"],
            "held_out": ["2026-01-01", "2026-08-01"],
        },
        "targets": {
            "train_origins_per_epoch": sum(ref.origins for ref in training_refs),
            "monitor_origins": int(monitor_origins),
            "validation_origins": int(validation_origins),
            "locked_test_origins": int(locked_test_origins),
        },
        "summaries": {
            "train": training_summary,
            **{
                name: _panel_summary(tuple(OfflineBlockRef(**row) for row in rows))
                for name, rows in panels.items()
            },
        },
        "panels": panels,
        "full_chunk_training": {
            "contract_version": FULL_CHUNK_CONTRACT_VERSION,
            "training_blocks": len(training_refs),
            "training_origins": sum(ref.origins for ref in training_refs),
            "training_catalog_hash": training_catalog_hash.hexdigest(),
            "training_population": "certified stable block index for 2019-01-01 through 2025-12-31",
            "monitor_pool_blocks": len(monitor_pool),
            "monitor_pool_origins": sum(ref.origins for ref in monitor_pool),
            "block_atomic": True,
            "epoch_sampling": "deterministic shuffled exact-ref partition without replacement",
            "chunk_replay": "exact stable block-reference membership",
        },
    }
    unsigned = dict(value)
    value["manifest_hash"] = _canonical_hash(unsigned)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(temporary, output_path)
    return value


def load_full_chunk_manifest(
    path: Path, *, shard_root: Path, config: DataConfig
) -> dict[str, Any]:
    value = load_discovery_manifest(path, shard_root=shard_root, config=config)
    contract = value.get("full_chunk_training")
    if not isinstance(contract, dict) or int(contract.get("contract_version", -1)) != FULL_CHUNK_CONTRACT_VERSION:
        raise RuntimeError("manifest is not a full-catalog chunk-training authority")
    for panel_name in (
        "epoch_train",
        "monitor_pool",
        "validation",
        "locked_test",
    ):
        rows = value.get("panels", {}).get(panel_name)
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"full-training manifest panel {panel_name!r} is empty")
    training = value["summaries"]["train"]
    if int(training["blocks"]) != int(contract["training_blocks"]) or int(
        training["origins"]
    ) != int(contract["training_origins"]):
        raise RuntimeError("full-training manifest coverage summary is inconsistent")
    if not str(contract.get("training_catalog_hash", "")):
        raise RuntimeError("full-training manifest has no training catalog hash")
    validation_dates = {
        (str(row["ticker"]), str(row["local_date"]))
        for row in value["panels"]["validation"]
    }
    locked_dates = {
        (str(row["ticker"]), str(row["local_date"]))
        for row in value["panels"]["locked_test"]
    }
    monitor_dates = {
        (str(row["ticker"]), str(row["local_date"]))
        for row in value["panels"]["monitor_pool"]
    }
    if validation_dates & locked_dates or validation_dates & monitor_dates or locked_dates & monitor_dates:
        raise RuntimeError("full-training held-out authorities overlap by ticker-date")
    return value


def load_full_training_refs(
    *,
    manifest_path: Path,
    units: Sequence[OfflineShardUnit],
    manifest: dict[str, Any],
) -> tuple[OfflineBlockRef, ...]:
    """Load and verify the compact stable block index used for chunk replay."""
    cache_path = manifest_path.parent / "full_catalog_index_v1" / "training.jsonl"
    if not cache_path.is_file():
        raise RuntimeError(
            f"full-training block index is missing: {cache_path}; rebuild the manifest"
        )
    unit_keys = tuple(unit.unit_key for unit in units)
    expected_unit_hash = hashlib.sha256("\n".join(unit_keys).encode("utf-8")).hexdigest()
    by_unit: dict[str, tuple[OfflineBlockRef, ...]] = {}
    with cache_path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid full-training block-index header: {cache_path}") from exc
        if (
            int(header.get("contract_version", -1)) != DISCOVERY_CONTRACT_VERSION
            or str(header.get("unit_keys_hash", "")) != expected_unit_hash
        ):
            raise RuntimeError("full-training block index does not match the certified units")
        for line_number, line in enumerate(handle, start=2):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                unit_key = str(row["unit_key"])
                refs = tuple(OfflineBlockRef(**item) for item in row["refs"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"invalid full-training block-index row {line_number}: {cache_path}"
                ) from exc
            if unit_key in by_unit:
                raise RuntimeError(f"duplicate unit in full-training block index: {unit_key}")
            by_unit[unit_key] = refs
    missing = [key for key in unit_keys if key not in by_unit]
    unexpected = sorted(set(by_unit) - set(unit_keys))
    if missing or unexpected:
        raise RuntimeError(
            "full-training block index unit coverage changed: "
            f"missing={missing[:5]} unexpected={unexpected[:5]}"
        )
    refs = tuple(ref for key in unit_keys for ref in by_unit[key])
    contract = manifest["full_chunk_training"]
    if (
        len(refs) != int(contract["training_blocks"])
        or sum(int(ref.origins) for ref in refs) != int(contract["training_origins"])
    ):
        raise RuntimeError("full-training block index totals do not match the manifest")
    digest = hashlib.sha256()
    for ref in refs:
        digest.update(_ref_identity(ref).encode("utf-8"))
        digest.update(b"\n")
    if digest.hexdigest() != str(contract["training_catalog_hash"]):
        raise RuntimeError("full-training block index hash does not match the manifest")
    return refs
