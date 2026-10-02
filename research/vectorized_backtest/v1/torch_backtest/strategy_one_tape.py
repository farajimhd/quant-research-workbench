"""Pack certified, immutable source rows into compact resident tensor banks.

Preparation may use Polars/NumPy for Arrow joins and layout. Decisions, funding,
fills and account updates belong to the Torch replay, after the current clock
has selected completed rows. This module never consumes baseline orders/fills.
"""

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
import polars as pl
import torch

from src.backend.backtest_market_data import market_day_boundary

MARKET_FIELDS = (
    "present",
    "price_valid",
    "extremes_valid",
    "open_int",
    "close_int",
    "high_int",
    "low_int",
    "bid_int",
    "ask_int",
    "bid_size",
    "ask_size",
    "quote_valid",
    "quote_age_us",
    "execution_volume",
    "macd_line",
    "macd_signal",
    "indicator_resolution_ms",
    "previous_bar_close_int",
)
FACT_FIELDS = (
    "candidate_valid",
    "episode_start_ms",
    "activation_gap",
    "bos_break_boundary_ms",
    "bos_support_level_id",
    "protection_valid",
    "stop",
    "target",
    "target_level_id",
    "target_ordinal",
)


@dataclass
class Tape:
    tickers: tuple[str, ...]
    through_ms: int
    market: dict
    facts: torch.Tensor
    geometry: torch.Tensor
    geometry_clock: torch.Tensor
    activation_clock: torch.Tensor
    broker: dict
    identities: tuple
    manifest: dict

    def to(self, device):
        """One transfer per bank. Dictionaries describe fixed compilation inputs."""

        def move(value):
            if isinstance(value, torch.Tensor):
                return value.to(device)
            if isinstance(value, dict):
                return {key: move(item) for key, item in value.items()}
            return value

        return Tape(
            self.tickers,
            self.through_ms,
            move(self.market),
            move(self.facts),
            move(self.geometry),
            move(self.geometry_clock),
            move(self.activation_clock),
            move(self.broker),
            self.identities,
            self.manifest,
        )


def _pack(frame, slots, n, fields, *, resolution):
    values = np.zeros((slots, n, len(fields)), dtype=np.float64)
    for name in ("macd_line", "macd_signal"):
        if name in fields:
            values[..., fields.index(name)] = np.nan
    if not frame.height:
        return torch.from_numpy(values)
    t = frame["boundary_ms"].to_numpy() // resolution - 1
    listing = frame["listing"].to_numpy()
    if np.any(t < 0) or np.any(t >= slots):
        raise ValueError("Source completed clocks exceed replay envelope")
    for channel, name in enumerate(fields):
        if name in frame.columns:
            values[t, listing, channel] = (
                frame[name]
                .fill_null(float("nan") if name in ("macd_line", "macd_signal") else 0)
                .cast(pl.Float64)
                .to_numpy()
            )
    return torch.from_numpy(values)


def _histogram(frame, slots, n, resolution):
    """Flatten ragged price histograms, preserving every eligible-price row.

    A per-interval pointer/count selects [N,Kmax] rows on device. Kmax is
    measured from data; no top-k clipping or capacity estimate is substituted.
    The zero sentinel makes masked gathers safe even for empty price lists.
    """
    counts = (
        frame["execution_price_levels"]
        .list.len()
        .fill_null(0)
        .to_numpy()
        .astype(np.int64)
    )
    starts = np.cumsum(np.r_[1, counts[:-1]])
    ptr = np.zeros((slots, n, 2), dtype=np.int64)
    ptr[
        frame["boundary_ms"].to_numpy() // resolution - 1,
        frame["listing"].to_numpy(),
        0,
    ] = starts
    ptr[
        frame["boundary_ms"].to_numpy() // resolution - 1,
        frame["listing"].to_numpy(),
        1,
    ] = counts
    prices = (
        frame.select("execution_price_levels")
        .explode("execution_price_levels")
        .unnest("execution_price_levels")
        .drop_nulls()
    )
    flat = np.zeros((1 + int(counts.sum()), 2), dtype=np.float64)
    if prices.height != int(counts.sum()):
        raise ValueError("Ragged price histogram lengths differ from source rows")
    flat[1:] = prices.select(pl.col("1").cast(pl.Float64), pl.col("2")).to_numpy()
    return {
        "pointers": torch.from_numpy(ptr),
        "prices": torch.from_numpy(flat),
        "maximum_prices": max(1, int(counts.max(initial=0))),
    }


def aggregate_liquidity(frame, resolution, origin_us):
    """Lossless capacity aggregation; sampled quote sizes are never summed.

    Fine bars are source evidence only. [start,end) source intervals become a
    completed coarse interval at end. Preserve first/last valid prices, full
    extremes, summed execution capacity, merged price levels and NBBO age.
    Sparse absence is zero activity under the certified source contract.
    """
    source = frame.sort("listing", "boundary_ms").with_columns(
        (((pl.col("boundary_ms") - 1) // resolution + 1) * resolution).alias(
            "boundary_ms"
        )
    )
    keys = ["boundary_ms", "listing"]
    price = pl.col("price_valid") == 1
    extreme = pl.col("extremes_valid") == 1
    quote = pl.col("quote_valid") == 1
    quotes = ("bid_int", "ask_int", "bid_size", "ask_size", "quote_timestamp_us")
    sums = tuple(
        name
        for name in (
            "volume",
            "notional",
            "execution_notional",
            "trade_count",
            "source_trade_count",
            "event_count",
            "quote_event_count",
        )
        if name in source.columns
    )
    cumulative = tuple(
        name
        for name in (
            "cumulative_volume",
            "cumulative_notional",
            "cumulative_execution_volume",
            "cumulative_execution_notional",
            "previous_close",
        )
        if name in source.columns
    )
    bars = source.group_by(keys).agg(
        pl.col("present").max(),
        pl.col("price_valid").max(),
        pl.col("extremes_valid").max(),
        pl.col("open_int").filter(price).first().fill_null(0),
        pl.col("close_int").filter(price).last().fill_null(0),
        pl.col("high_int").filter(extreme).max().fill_null(0),
        pl.col("low_int").filter(extreme & (pl.col("low_int") > 0)).min().fill_null(0),
        *[pl.col(name).filter(quote).last().fill_null(0) for name in quotes],
        pl.col("quote_valid").max(),
        pl.col("execution_volume").sum(),
        *[pl.col(name).sum() for name in sums],
        *[pl.col(name).last() for name in cumulative],
        *[pl.col(name).min() for name in ("first_event_us",) if name in source.columns],
        *[pl.col(name).max() for name in ("last_event_us",) if name in source.columns],
    )
    # Reduce equal-price capacities rather than growing the ragged histogram
    # tenfold. Keep every price level; no top-k clipping or invented liquidity.
    levels = (
        source.select(*keys, "execution_price_levels")
        .explode("execution_price_levels")
        .unnest("execution_price_levels")
        .drop_nulls("1")
        .group_by(*keys, "1")
        .agg(pl.col("2").sum())
        .sort(*keys, "1")
        .group_by(keys)
        .agg(pl.struct("1", "2").alias("execution_price_levels"))
    )
    bars = bars.join(levels, on=keys, how="left").sort("listing", "boundary_ms")
    if "execution_notional" in bars.columns:
        bars = bars.with_columns(
            pl.when(pl.col("execution_volume") > 0)
            .then(pl.col("execution_notional") / pl.col("execution_volume"))
            .otherwise(None)
            .alias("execution_vwap")
        )
    return bars.with_columns(
        ((pl.col("ask_int") - pl.col("bid_int")) / 10000).alias("spread"),
        pl.lit(resolution).alias("resolution_ms"),
        (
            origin_us
            + pl.col("boundary_ms") * 1000
            - pl.col("quote_timestamp_us").cast(pl.Int64)
        ).alias("quote_age_us"),
        pl.lit(float("nan")).alias("macd_line"),
        pl.lit(float("nan")).alias("macd_signal"),
        pl.lit(0).alias("indicator_resolution_ms"),
        pl.when(pl.col("price_valid") == 1)
        .then(pl.col("close_int"))
        .otherwise(None)
        .forward_fill()
        .shift(1)
        .over("listing")
        .fill_null(0)
        .alias("previous_bar_close_int"),
    )


def prepare(directory, *, maximum_bytes=8 * 1024**3, clock_ms=100):
    """Validate hashes/keys, then pack a bounded main-clock strategy tape.

    clock_ms=100 preserves the faithful comparison. The unified 500/1000ms
    variants aggregate source liquidity before packing and retain no sub-clock
    feature bank. Displayed sizes are sampled, never summed. Orders must exist
    by the completed interval's start to fill. Certified candidate events are
    sampled at the latest event in each completed main-clock bucket; this is
    an explicit research approximation, not a new upstream search universe.
    """
    if clock_ms != 100:
        from .unified_clock import validate_clock

        validate_clock(clock_ms)
    directory = Path(directory).resolve()
    root = Path("D:/TradingML/runtimes").resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Tape cache must belong to the configured runtime root")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get(
        "version"
    ) != "strategy-one-certified-market-tape-v1" or not manifest.get("facts_sha256"):
        raise ValueError("Tape requires a versioned manifest and producer-fact hash")
    facts_bytes = (directory / "facts.json").read_bytes()
    if sha256(facts_bytes).hexdigest() != manifest["facts_sha256"]:
        raise ValueError("Certified producer-fact cache hash changed")
    source = json.loads(facts_bytes.decode("utf-8"))
    tickers = tuple(row["ticker"] for row in manifest["files"])
    if manifest["through_ms"] % clock_ms:
        raise ValueError(
            "Session end must align with the main clock; no tail truncation"
        )
    slots, n = manifest["through_ms"] // clock_ms, len(tickers)
    frames = []
    for listing, row in enumerate(manifest["files"]):
        path = Path(row["path"]).resolve()
        if (
            not path.is_relative_to(directory)
            or sha256(path.read_bytes()).hexdigest() != row["sha256"]
        ):
            raise ValueError("Certified market cache path/hash changed")
        frame = pl.read_parquet(path)
        if frame.height != row["rows"] or frame["ticker"].unique().to_list() != [
            row["ticker"]
        ]:
            raise ValueError("Market cache ticker/count differs from its manifest")
        if (
            frame.select(pl.struct("boundary_ms", "resolution_ms").n_unique()).item()
            != frame.height
        ):
            raise ValueError("Duplicate source bar keys")
        frames.append(
            frame.with_columns(
                pl.lit(listing).alias("listing"), pl.lit(1).alias("present")
            )
        )
    full = pl.concat(frames).sort("listing", "boundary_ms", "resolution_ms")
    sessions = full["session_date"].unique().to_list()
    if len(sessions) != 1 or (
        manifest.get("session_date")
        and manifest["session_date"] != sessions[0].isoformat()
    ):
        raise ValueError("Tape session differs from its certified manifest")
    origin = round(market_day_boundary(sessions[0], 0).timestamp() * 1_000_000)
    market100 = full.filter(pl.col("resolution_ms") == 100).with_columns(
        (
            origin
            + pl.col("boundary_ms") * 1000
            - pl.col("quote_timestamp_us").cast(pl.Int64)
        ).alias("quote_age_us"),
        pl.when(pl.col("price_valid") == 1)
        .then(pl.col("close_int"))
        .otherwise(None)
        .forward_fill()
        .shift(1)
        .over("listing")
        .fill_null(0)
        .alias("previous_bar_close_int"),
    )
    base = (
        market100
        if clock_ms == 100
        else aggregate_liquidity(market100, clock_ms, origin)
    )
    if clock_ms == 1000:
        indicators = full.filter(pl.col("resolution_ms") == 1000).select(
            "boundary_ms",
            "listing",
            "macd_line",
            "macd_signal",
            "indicator_resolution_ms",
        )
        base = base.drop("macd_line", "macd_signal", "indicator_resolution_ms").join(
            indicators, on=["boundary_ms", "listing"], how="left"
        )
    market = {clock_ms: _pack(base, slots, n, MARKET_FIELDS, resolution=clock_ms)}
    for resolution in (1000, 30_000):
        if resolution == clock_ms:
            continue
        subset = full.filter(pl.col("resolution_ms") == resolution)
        market[resolution] = _pack(
            subset,
            manifest["through_ms"] // resolution,
            n,
            MARKET_FIELDS,
            resolution=resolution,
        )

    # Lexically ordered lossless per-listing ID dictionaries include BOS and
    # target identities even when they are absent from current geometry.
    dictionaries = []
    for ticker in tickers:
        ids = {
            row["level_id"]
            for name, rows in source["v7_intervals"]
            if name == ticker
            for row in rows
        }
        ids.update(
            row[field]
            for row in source["candidates"]
            if row["ticker"] == ticker
            for field in ("bos_support_level_id", "target_level_id")
            if row[field]
        )
        dictionaries.append(("", *sorted(ids)))
    identity_maps = [
        {identity: index for index, identity in enumerate(ids)} for ids in dictionaries
    ]
    d = max(map(len, dictionaries))
    seconds = manifest["through_ms"] // 1000
    estimated = (seconds + 1) * n * d * 4 * 8 + slots * n * (
        len(MARKET_FIELDS) + len(FACT_FIELDS) + 4
    ) * 8
    if estimated > maximum_bytes:
        raise MemoryError(
            f"Declared resident bank estimate {estimated:,} exceeds {maximum_bytes:,}"
        )
    geometry = np.zeros((seconds + 1, n, d, 4), dtype=np.float64)
    gclock = np.zeros((slots, n), dtype=np.int64)
    activation_clock = np.zeros((slots, n), dtype=np.int64)
    clocks = dict(source["v7_clocks"])
    for listing, ticker in enumerate(tickers):
        for name, rows in source["v7_intervals"]:
            if name != ticker:
                continue
            for row in rows:
                start, end = (
                    row["valid_from_ms"] // 1000,
                    min(seconds + 1, (row["valid_to_ms"] + 999) // 1000),
                )
                if start >= end:
                    continue
                identity = identity_maps[listing][row["level_id"]]
                eligible = row["role"] == "resistance" or (
                    row["role"] == "transition"
                    and row["transition_from"] == "resistance"
                )
                geometry[start:end, listing, identity] = (
                    row["lower"],
                    row["upper"],
                    eligible,
                    row["role"] == "resistance",
                )
        valid = np.r_[0, np.asarray(clocks[ticker], dtype=np.int64)]
        boundaries = np.arange(1, slots + 1, dtype=np.int64) * clock_ms
        gclock[:, listing] = valid[np.searchsorted(valid, boundaries, side="right") - 1]
        starts = np.asarray(
            sorted(
                {
                    row["episode_start_ms"]
                    for row in source["activations"]
                    if row["ticker"] == ticker
                }
            ),
            dtype=np.int64,
        )
        starts = starts[(starts > 0) & (starts <= manifest["through_ms"])]
        activation_clock[(starts - 1) // clock_ms, listing] = starts
    facts = np.zeros((slots, n, len(FACT_FIELDS)), dtype=np.float64)
    gaps = {
        (row["ticker"], row["episode_start_ms"]): row["average_gap"]
        for row in source["activations"]
    }
    for row in sorted(source["candidates"], key=lambda row: row["boundary_ms"]):
        listing = tickers.index(row["ticker"])
        values = (
            1,
            row["episode_start_ms"],
            gaps[row["ticker"], row["episode_start_ms"]],
            row["bos_break_boundary_ms"],
            identity_maps[listing][row["bos_support_level_id"]],
            row["protection_valid"],
            row["stop_price"],
            row["target_price"],
            identity_maps[listing][row["target_level_id"]],
            row["target_ordinal"],
        )
        facts[(row["boundary_ms"] - 1) // clock_ms, listing] = values
    if clock_ms != 100:
        # Matching sees only the completed main-clock interval. Fine source
        # buckets never become replay steps or searched feature dependencies.
        broker = {
            clock_ms: {
                "market": _pack(base, slots, n, MARKET_FIELDS, resolution=clock_ms),
                **_histogram(base, slots, n, clock_ms),
            }
        }
    else:
        broker = {100: {"market": market[100], **_histogram(market100, slots, n, 100)}}
        # The coarse broker contract changes fill approximation only, never the
        # certified 100ms strategy observations or their source-history clocks.
        coarse = market100.with_columns(
            ((pl.col("boundary_ms") - 1) // 1000 + 1).mul(1000).alias("boundary_ms")
        )
        last_fields = [
            name
            for name in MARKET_FIELDS
            if name
            not in {
                "high_int",
                "low_int",
                "extremes_valid",
                "execution_volume",
                "price_valid",
            }
        ]
        # quote_age_us was relative to the last observed 100ms boundary. Add its
        # distance to this completed second rather than introducing a future quote.
        bars = (
            coarse.group_by("boundary_ms", "listing")
            .agg(
                *[pl.col(name).last() for name in last_fields],
                pl.col("price_valid").max(),
                pl.col("extremes_valid").max(),
                pl.when(pl.col("extremes_valid") == 1)
                .then(pl.col("high_int"))
                .otherwise(0)
                .max()
                .alias("high_int"),
                pl.when((pl.col("extremes_valid") == 1) & (pl.col("low_int") > 0))
                .then(pl.col("low_int"))
                .otherwise(None)
                .min()
                .fill_null(0)
                .alias("low_int"),
                pl.col("execution_volume").sum(),
                pl.col("execution_price_levels")
                .flatten()
                .drop_nulls()
                .alias("execution_price_levels"),
                pl.col("quote_timestamp_us").last(),
            )
            .with_columns(
                (
                    origin
                    + pl.col("boundary_ms") * 1000
                    - pl.col("quote_timestamp_us").cast(pl.Int64)
                ).alias("quote_age_us")
            )
        )
        broker[1000] = {
            "market": _pack(bars, seconds, n, MARKET_FIELDS, resolution=1000),
            **_histogram(bars, seconds, n, 1000),
        }
    manifest = {
        **manifest,
        "listing_count": n,
        "clock_ms": clock_ms,
        "policy_contract": "unified-strategy-one-causal-v2"
        if clock_ms != 100
        else "faithful-strategy-one-v1",
        "candidate_sampling": "latest certified candidate in each completed interval",
        "feature_resolutions_ms": sorted(market),
        "strategy_slots": slots,
        "identities": d,
        "resident_estimate_bytes": estimated,
    }
    return Tape(
        tickers,
        manifest["through_ms"],
        market,
        torch.from_numpy(facts),
        torch.from_numpy(geometry),
        torch.from_numpy(gclock),
        torch.from_numpy(activation_clock),
        broker,
        tuple(dictionaries),
        manifest,
    )
