"""Normalize certified sparse ARTE projections into a bounded resident tape.

Polars/NumPy are used only at this input boundary. Torch replay never constructs
a dataframe, reads a database, or transfers a per-step observation to the GPU.
"""

from dataclasses import dataclass
from math import gcd
from time import perf_counter

import numpy as np
import polars as pl
import torch

from research.vectorized_backtest.v4.torch_backtest.encoding.clickhouse import (
    PreparedSession,
    _clocks,
)
from research.vectorized_backtest.v4.torch_backtest.encoding.core import EncodingError

from .compiler import TorchStrategy


@dataclass(frozen=True)
class WindowBank:
    """Packed actual-source records [M,F], with a causal [T,N] head cursor.

    Records are contiguous per listing. `offsets` prevents a lag from crossing
    listing boundaries. The lag axis is gathered on device only when requested.
    """

    labels: tuple
    values: torch.Tensor
    available: torch.Tensor
    cursor: torch.Tensor
    offsets: torch.Tensor

    def gather(self, row, clock, length):
        """Return [N,F,H] for a model, or for inspecting the feature dimensions.

        H is newest-first; fields follow `labels`. Each resolution/source bank
        has its own chronological cursor, validity and availability evidence.
        """
        head = self.cursor.index_select(0, row).squeeze(0)
        index = head[:, None] - torch.arange(length, device=head.device)[None, :]
        valid = (head[:, None] > 0) & (index >= self.offsets[:, None])
        safe = index.clamp_min(0)
        values, available = self.values[safe], self.available[safe]  # [N,H,F]
        return torch.where(
            valid[:, :, None] & (available <= clock), values, float("nan")
        ).permute(0, 2, 1)

    def window(self, row, clock, label, length):
        head = self.cursor.index_select(0, row).squeeze(0)  # [N]
        index = head[:, None] - torch.arange(length, device=head.device)[None, :]
        valid = (head[:, None] > 0) & (index >= self.offsets[:, None])
        column = self.labels.index(label)
        safe = index.clamp_min(0)
        values = self.values[:, column][safe]  # [N,H], newest to oldest
        available = self.available[:, column][safe]
        return torch.where(valid & (available <= clock), values, float("nan"))


@dataclass(frozen=True)
class TensorTape:
    """Stable listing axis and completed data on the common clock grid.

    market [T,N,F] float64; broker [T,N,6] float64; admitted [N] int64.
    Broker fields: close, valid, volume, VWAP, latest reference price/validity.
    T includes the final boundary and any observation warm-up prefix.
    """

    prepared: PreparedSession
    listing_ids: tuple[str, ...]
    labels: tuple[int, ...]
    market: torch.Tensor
    broker: torch.Tensor
    admitted: torch.Tensor
    first_us: int
    start_us: int
    end_us: int
    origin_us: int
    step_us: int
    metrics: dict
    windows: dict

    @property
    def device(self):
        return self.market.device


def to_tensors(
    prepared: PreparedSession, strategy: TorchStrategy, *, device="cpu", max_gib=4.0
) -> TensorTape:
    """One-time projection/alignment/transfer with an explicit dense-memory guard.

    Each physical column carries its own latest nonnull update. A NaN source
    value IS an update and masks the feature; it is not forward-filled away.
    Whole-session admission is only an allocation envelope: the device mask
    prevents trading before each actual admission timestamp.
    """
    started = perf_counter()
    config = prepared.config
    _, origin, start, end = _clocks(config)
    step = gcd(config.strategy_ms, config.broker_ms) * 1000
    first = max(origin + 14_400_000_000, start - config.warmup_seconds * 1_000_000)
    first = ((first + step - 1) // step) * step
    slots = (end - first) // step + 1
    watchlist = prepared.watchlist.sort("listing_id")
    listings = tuple(watchlist["listing_id"].to_list())
    if len(set(listings)) != len(listings) or watchlist["admitted_at_us"].null_count():
        raise EncodingError("Invalid watchlist identity/admission keys")
    dependencies = tuple(f for f in strategy.dependencies if f.source != "state")
    envelope = {f.label: f for f in prepared.dependencies}
    if any(envelope.get(f.label) != f for f in dependencies):
        raise EncodingError("Strategy lies outside the prepared dependency envelope")
    n, fields = len(listings), len(dependencies)
    projected_bytes = slots * n * (fields + 6) * 8 + n * 8
    window_groups = {}
    for _, _, feature, _, _ in strategy.source_windows:
        window_groups.setdefault((feature.source, feature.resolution_ms), {})[
            feature.label
        ] = feature
    projected_bytes += sum(
        slots * n * 8
        + n * 8
        + (prepared.features[resolution].height + 1) * len(features) * 16
        for (_, resolution), features in window_groups.items()
    )
    if not np.isfinite(max_gib) or max_gib <= 0 or projected_bytes > max_gib * 1024**3:
        raise MemoryError(
            f"Resident tape needs {projected_bytes / 1024**3:.3f} GiB; cap={max_gib}"
        )
    device = torch.device(device)
    if device.type == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA requested but unavailable; no automatic CPU fallback"
            )
        free, _ = torch.cuda.mem_get_info(device)
        if projected_bytes > free * 0.7:
            raise MemoryError(
                "Tape would consume over 70% of currently free device memory"
            )
    indices = {listing: i for i, listing in enumerate(listings)}
    clocks = np.arange(slots, dtype=np.int64)[:, None] * step + first

    def coordinates(frame):
        if (
            not frame.schema["time_us"].is_integer()
            or frame["time_us"].null_count()
            or frame["listing_id"].null_count()
        ):
            raise EncodingError(
                "Source identity and integer UTC microseconds are required"
            )
        if frame.select("listing_id", "time_us").is_duplicated().any():
            raise EncodingError("Duplicate source listing/clock key")
        times = frame["time_us"].to_numpy()
        tick = (times + step - 1) // step * step
        rows = (tick - first) // step
        try:
            columns = np.array(
                [indices[x] for x in frame["listing_id"].to_list()], dtype=np.int64
            )
        except KeyError as error:
            raise EncodingError("Non-watchlisted source row") from error
        return rows, columns

    last_key, last_update = None, None

    def column(frame, name, coords, *, carry):
        # Flattened source indices choose the last update after common-clock
        # coalescing. A maximum scan propagates indices, not possibly-NaN values.
        rows, columns = coords
        source = frame[name].to_numpy().astype(np.float64, copy=False)
        nonnull = frame[name].is_not_null().to_numpy()
        # Reuse scans only when the complete nonnull update bitmap agrees.
        # NaN is still an update. Keep one grid, not a cache per source field.
        nonlocal last_key, last_update
        key = (id(frame), carry, np.packbits(nonnull).tobytes())
        if key != last_key:
            valid = nonnull & (rows >= 0) & (rows < slots)
            update = np.zeros((slots, n), dtype=np.int64)
            np.maximum.at(
                update,
                (rows[valid], columns[valid]),
                np.arange(len(source), dtype=np.int64)[valid] + 1,
            )
            if carry:
                np.maximum.accumulate(update, axis=0, out=update)
            last_key, last_update = key, update
        return np.concatenate(([np.nan], source))[last_update]

    # Normalize one atomic field at a time to bound temporary host memory.
    normalized = np.empty((slots, n, fields), dtype=np.float64)
    frames = {r: f.sort("time_us") for r, f in prepared.features.items()}
    positions = {r: coordinates(f) for r, f in frames.items()}
    for index, feature in enumerate(dependencies):
        frame, coords = frames[feature.resolution_ms], positions[feature.resolution_ms]
        if (
            feature.available_at_column
            and not frame.schema[feature.available_at_column].is_integer()
        ):
            raise EncodingError("Availability must use integer UTC microseconds")
        value = column(frame, feature.column, coords, carry=True) * feature.scale
        valid = np.isfinite(value)
        if feature.valid_column:
            valid &= column(frame, feature.valid_column, coords, carry=True) == 1
        if feature.available_at_column:
            valid &= (
                column(frame, feature.available_at_column, coords, carry=True) <= clocks
            )
        normalized[:, :, index] = np.where(valid, value, np.nan)
    bars = prepared.broker_bars.sort("time_us")
    if bars.filter(
        pl.any_horizontal(pl.col("close", "volume", "valid").is_null())
        | ~pl.col("close").is_finite()
        | ~pl.col("volume").is_finite()
        | (pl.col("volume") < 0)
        | (
            (pl.col("volume") > 0)
            & (~pl.col("vwap").is_finite().fill_null(False) | (pl.col("vwap") <= 0))
        )
    ).height:
        raise EncodingError("Invalid execution price/capacity evidence")
    coords = coordinates(bars)
    broker = np.empty((slots, n, 6), dtype=np.float64)
    for index, name in enumerate(("close", "valid", "volume", "vwap")):
        values = column(bars, name, coords, carry=False)
        broker[:, :, index] = np.nan_to_num(values, nan=0.0)
    broker[:, :, 4] = column(bars, "close", coords, carry=True)
    broker[:, :, 5] = column(bars, "valid", coords, carry=True)
    banks = {}
    for (lane, resolution), feature_map in window_groups.items():
        features = tuple(feature_map[k] for k in sorted(feature_map))
        frame = (
            frames[resolution]
            .filter(
                pl.any_horizontal(
                    [pl.col(f.available_at_column).is_not_null() for f in features]
                )
            )
            .sort(["listing_id", "time_us"])
        )
        coords = coordinates(frame)
        rows, columns = coords
        cursor = np.zeros((slots, n), dtype=np.int64)
        eligible = (rows >= 0) & (rows < slots)
        np.maximum.at(
            cursor,
            (rows[eligible], columns[eligible]),
            np.arange(frame.height)[eligible] + 1,
        )
        np.maximum.accumulate(cursor, axis=0, out=cursor)
        counts = np.bincount(columns, minlength=n)
        offsets = np.concatenate(([1], 1 + np.cumsum(counts)[:-1]))[:n]
        values = np.full((frame.height + 1, len(features)), np.nan)
        available = np.zeros((frame.height + 1, len(features)), dtype=np.int64)
        for index, feature in enumerate(features):
            field = frame[feature.column].to_numpy().astype(np.float64) * feature.scale
            valid = np.isfinite(field)
            if feature.valid_column:
                valid &= frame[feature.valid_column].fill_null(False).to_numpy() == 1
            values[1:, index] = np.where(valid, field, np.nan)
            available[1:, index] = (
                frame[feature.available_at_column].fill_null(2**63 - 1).to_numpy()
            )
        banks[(lane, resolution)] = (
            tuple(f.label for f in features),
            values,
            available,
            cursor,
            offsets,
        )
    last_update = None
    build_seconds = perf_counter() - started
    transfer_start = perf_counter()
    market_tensor = torch.from_numpy(normalized).to(device)
    broker_tensor = torch.from_numpy(broker).to(device)
    admitted = torch.tensor(
        watchlist["admitted_at_us"].to_list(), dtype=torch.int64, device=device
    )
    windows = {
        key: WindowBank(
            labels,
            torch.from_numpy(values).to(device),
            torch.from_numpy(available).to(device),
            torch.from_numpy(cursor).to(device),
            torch.from_numpy(offsets).to(device),
        )
        for key, (labels, values, available, cursor, offsets) in banks.items()
    }
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    metrics = {
        "alignment_seconds": build_seconds,
        "transfer_seconds": perf_counter() - transfer_start,
        "resident_gib": projected_bytes / 1024**3,
        "grid_slots": slots,
        "listings": n,
        "market_fields": fields,
    }
    return TensorTape(
        prepared,
        listings,
        tuple(f.label for f in dependencies),
        market_tensor,
        broker_tensor,
        admitted,
        first,
        start,
        end,
        origin,
        step,
        metrics,
        windows,
    )
