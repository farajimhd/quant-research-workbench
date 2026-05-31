from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "research" / "masked_event_model").exists()
    ),
    Path(__file__).resolve().parents[2],
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v2.config import DataConfig
from research.masked_event_model.v2.data import count_chunk_rows, frame_to_arrays, load_chunk_block, valid_origins
from research.masked_event_model.v2.schema import QUOTE_FEATURE_COLUMNS, TRADE_FEATURE_COLUMNS


DEFAULT_CHUNK_ROOT = Path(
    "//DESKTOP-SAAI85T/Workstation-D/market-data/flatfiles/us_stocks_sip/derived/event_chunks_v2"
)
DEFAULT_OUTPUT = Path("D:/TradingML/runtimes/analysis/price_tick_encoding_report.json")

QUOTE = {name: index for index, name in enumerate(QUOTE_FEATURE_COLUMNS)}
TRADE = {name: index for index, name in enumerate(TRADE_FEATURE_COLUMNS)}


@dataclass(slots=True)
class ChunkFile:
    ticker: str
    year_month: str
    path: Path


@dataclass(slots=True)
class RunningStats:
    name: str
    candidate_bits: tuple[int, ...]
    reservoir_size: int = 1_000_000
    count: int = 0
    minimum: int | None = None
    maximum: int | None = None
    sum_value: float = 0.0
    sum_sq: float = 0.0
    coverage_counts: dict[int, int] = field(default_factory=dict)
    reservoir: list[np.ndarray] = field(default_factory=list)
    reservoir_count: int = 0

    def __post_init__(self) -> None:
        self.coverage_counts = {bits: 0 for bits in self.candidate_bits}

    def update(self, values: np.ndarray) -> None:
        values = np.asarray(values)
        if values.size == 0:
            return
        values = values[np.isfinite(values)].astype(np.int64, copy=False).reshape(-1)
        if values.size == 0:
            return
        abs_values = np.abs(values)
        self.count += int(values.size)
        current_min = int(values.min())
        current_max = int(values.max())
        self.minimum = current_min if self.minimum is None else min(self.minimum, current_min)
        self.maximum = current_max if self.maximum is None else max(self.maximum, current_max)
        self.sum_value += float(values.sum(dtype=np.float64))
        self.sum_sq += float(np.square(values.astype(np.float64)).sum(dtype=np.float64))
        for bits in self.candidate_bits:
            self.coverage_counts[bits] += int((abs_values <= ((1 << bits) - 1)).sum())
        self._update_reservoir(values)

    def _update_reservoir(self, values: np.ndarray) -> None:
        remaining = self.reservoir_size - self.reservoir_count
        if remaining > 0:
            keep = values[:remaining].copy()
            self.reservoir.append(keep)
            self.reservoir_count += int(keep.size)
            values = values[remaining:]
        if values.size == 0:
            return
        # Deterministic tail sampling keeps memory bounded without spending time on
        # per-value reservoir replacement for this exploratory distribution scan.
        stride = max(1, int(math.ceil(values.size / max(1, self.reservoir_size // 20))))
        sampled = values[::stride].copy()
        if sampled.size:
            self.reservoir.append(sampled)
            self.reservoir_count += int(sampled.size)

    def summary(self) -> dict[str, Any]:
        if self.count == 0:
            return {"name": self.name, "count": 0}
        sample = np.concatenate(self.reservoir).astype(np.int64, copy=False) if self.reservoir else np.empty(0, dtype=np.int64)
        if sample.size > self.reservoir_size:
            step = max(1, sample.size // self.reservoir_size)
            sample = sample[::step][: self.reservoir_size]
        quantiles = {}
        abs_quantiles = {}
        if sample.size:
            for q in (50, 90, 95, 99, 99.5, 99.9, 99.99):
                quantiles[f"p{q}"] = float(np.percentile(sample, q))
                abs_quantiles[f"p{q}_abs"] = float(np.percentile(np.abs(sample), q))
        mean = self.sum_value / self.count
        variance = max(0.0, self.sum_sq / self.count - mean * mean)
        return {
            "name": self.name,
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": mean,
            "std": math.sqrt(variance),
            "quantiles": quantiles,
            "abs_quantiles": abs_quantiles,
            "coverage_pct_by_magnitude_bits": {
                str(bits): 100.0 * covered / self.count for bits, covered in self.coverage_counts.items()
            },
        }


@dataclass(slots=True)
class AnomalyStats:
    invalid_anchor_count: int = 0
    quote_invalid_count: int = 0
    trade_invalid_count: int = 0
    crossed_quote_count: int = 0
    crossed_trade_quote_count: int = 0
    anchor_sub_dollar_count: int = 0
    anchor_at_or_above_dollar_count: int = 0
    price_crossed_tick_regime_count: int = 0
    quote_rounding_residual_count: int = 0
    trade_rounding_residual_count: int = 0
    max_quote_rounding_residual_ticks: float = 0.0
    max_trade_rounding_residual_ticks: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze tick-based price deltas in saved event chunks.")
    parser.add_argument("--chunk-root", type=Path, default=DEFAULT_CHUNK_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-date", default="2025-11-01")
    parser.add_argument("--end-date", default="2025-12-05")
    parser.add_argument("--tickers", default="ALL", help="Comma-separated tickers or ALL.")
    parser.add_argument("--chunk-ms", type=int, default=500)
    parser.add_argument("--context-seconds", type=int, default=30)
    parser.add_argument("--max-files", type=int, default=120)
    parser.add_argument("--max-blocks-per-file", type=int, default=4)
    parser.add_argument("--max-origins-per-file", type=int, default=160)
    parser.add_argument("--row-block-size", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--candidate-bits", default="2,3,4,5,6,7,8,10,12,14,16")
    parser.add_argument("--progress-every-files", type=int, default=5)
    return parser.parse_args()


def tick_unit_from_anchor(anchor_mid: float) -> float:
    return 0.0001 if anchor_mid < 1.0 else 0.01


def month_range(start_date: str, end_date: str) -> tuple[str, ...]:
    start_year, start_month = (int(part) for part in start_date[:7].split("-"))
    end_year, end_month = (int(part) for part in end_date[:7].split("-"))
    months: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year += 1
            month = 1
    return tuple(months)


def chunk_layout_root(root: Path, chunk_ms: int) -> Path:
    nested = root / f"chunk_ms={chunk_ms}" / "mq=128_mt=192_m=256"
    return nested if nested.exists() else root


def discover_files_fast(
    root: Path,
    *,
    start_date: str,
    end_date: str,
    tickers: tuple[str, ...],
    chunk_ms: int,
    max_files: int,
    rng: random.Random,
) -> list[ChunkFile]:
    layout_root = chunk_layout_root(root, chunk_ms)
    months = month_range(start_date, end_date)
    use_all = len(tickers) == 1 and tickers[0] in {"ALL", "*", "__ALL_TICKERS__"}
    files: list[ChunkFile] = []
    if use_all:
        ticker_dirs = (path for path in layout_root.iterdir() if path.is_dir() and path.name.startswith("ticker="))
        for ticker_dir in ticker_dirs:
            ticker = ticker_dir.name.split("=", 1)[1].upper()
            for year_month in months:
                path = ticker_dir / f"{year_month}.parquet"
                if path.exists():
                    files.append(ChunkFile(ticker=ticker, year_month=year_month, path=path))
                    if max_files > 0 and len(files) >= max_files:
                        return files
    else:
        for ticker in tickers:
            ticker_dir = layout_root / f"ticker={ticker}"
            for year_month in months:
                path = ticker_dir / f"{year_month}.parquet"
                if path.exists():
                    files.append(ChunkFile(ticker=ticker, year_month=year_month, path=path))
                    if max_files > 0 and len(files) >= max_files:
                        return files
    return files


def price_to_ticks(price: np.ndarray, tick_unit: float) -> tuple[np.ndarray, np.ndarray]:
    raw_ticks = price / tick_unit
    ticks = np.rint(raw_ticks).astype(np.int64)
    residual = np.abs(raw_ticks - ticks)
    return ticks, residual


def sign_magnitude_bits_required(max_abs_value: float | int) -> int:
    value = int(math.ceil(float(max_abs_value)))
    if value <= 0:
        return 0
    return int(math.ceil(math.log2(value + 1)))


def update_price_rounding_anomalies(
    residual: np.ndarray,
    *,
    threshold: float,
    quote: bool,
    anomalies: AnomalyStats,
) -> None:
    if residual.size == 0:
        return
    count = int((residual > threshold).sum())
    maximum = float(residual.max(initial=0.0))
    if quote:
        anomalies.quote_rounding_residual_count += count
        anomalies.max_quote_rounding_residual_ticks = max(anomalies.max_quote_rounding_residual_ticks, maximum)
    else:
        anomalies.trade_rounding_residual_count += count
        anomalies.max_trade_rounding_residual_ticks = max(anomalies.max_trade_rounding_residual_ticks, maximum)


def analyze_origin(
    arrays: dict[str, np.ndarray],
    origin: int,
    context_chunks: int,
    stats: dict[str, RunningStats],
    anomalies: AnomalyStats,
) -> None:
    anchor = float(arrays["current_mid"][origin])
    if not math.isfinite(anchor) or anchor <= 0.0:
        anomalies.invalid_anchor_count += 1
        return
    if anchor < 1.0:
        anomalies.anchor_sub_dollar_count += 1
    else:
        anomalies.anchor_at_or_above_dollar_count += 1
    tick_unit = tick_unit_from_anchor(anchor)
    anchor_ticks = int(round(anchor / tick_unit))

    start = origin - context_chunks + 1
    end = origin + 1

    quotes = arrays["quote_values"][start:end].reshape(-1, len(QUOTE_FEATURE_COLUMNS))
    bid = quotes[:, QUOTE["bid_price"]]
    ask = quotes[:, QUOTE["ask_price"]]
    quote_valid = np.isfinite(bid) & np.isfinite(ask) & (bid > 0.0) & (ask > 0.0)
    if quote_valid.any():
        bid = bid[quote_valid]
        ask = ask[quote_valid]
        anomalies.crossed_quote_count += int((ask < bid).sum())
        anomalies.price_crossed_tick_regime_count += int(((bid < 1.0) | (ask < 1.0)).sum()) if anchor >= 1.0 else int(((bid >= 1.0) | (ask >= 1.0)).sum())
        bid_ticks, bid_residual = price_to_ticks(bid, tick_unit)
        ask_ticks, ask_residual = price_to_ticks(ask, tick_unit)
        update_price_rounding_anomalies(bid_residual, threshold=1e-3, quote=True, anomalies=anomalies)
        update_price_rounding_anomalies(ask_residual, threshold=1e-3, quote=True, anomalies=anomalies)
        spread_ticks = ask_ticks - bid_ticks
        stats["quote_external_bid_delta_ticks"].update(bid_ticks - anchor_ticks)
        stats["quote_external_ask_delta_ticks"].update(ask_ticks - anchor_ticks)
        stats["quote_external_mid_delta_half_ticks"].update(bid_ticks + ask_ticks - 2 * anchor_ticks)
        stats["quote_local_bid_offset_half_ticks"].update(-spread_ticks)
        stats["quote_local_ask_offset_half_ticks"].update(spread_ticks)
        stats["quote_spread_ticks"].update(spread_ticks)
    else:
        anomalies.quote_invalid_count += 1

    trades = arrays["trade_values"][start:end].reshape(-1, len(TRADE_FEATURE_COLUMNS))
    price = trades[:, TRADE["price"]]
    latest_bid = trades[:, TRADE["latest_bid"]]
    latest_ask = trades[:, TRADE["latest_ask"]]
    trade_valid = (
        np.isfinite(price)
        & np.isfinite(latest_bid)
        & np.isfinite(latest_ask)
        & (price > 0.0)
        & (latest_bid > 0.0)
        & (latest_ask > 0.0)
    )
    if trade_valid.any():
        price = price[trade_valid]
        latest_bid = latest_bid[trade_valid]
        latest_ask = latest_ask[trade_valid]
        anomalies.crossed_trade_quote_count += int((latest_ask < latest_bid).sum())
        anomalies.price_crossed_tick_regime_count += int(((price < 1.0) | (latest_bid < 1.0) | (latest_ask < 1.0)).sum()) if anchor >= 1.0 else int(((price >= 1.0) | (latest_bid >= 1.0) | (latest_ask >= 1.0)).sum())
        price_ticks, price_residual = price_to_ticks(price, tick_unit)
        latest_bid_ticks, latest_bid_residual = price_to_ticks(latest_bid, tick_unit)
        latest_ask_ticks, latest_ask_residual = price_to_ticks(latest_ask, tick_unit)
        update_price_rounding_anomalies(price_residual, threshold=1e-3, quote=False, anomalies=anomalies)
        update_price_rounding_anomalies(latest_bid_residual, threshold=1e-3, quote=False, anomalies=anomalies)
        update_price_rounding_anomalies(latest_ask_residual, threshold=1e-3, quote=False, anomalies=anomalies)
        latest_spread_ticks = latest_ask_ticks - latest_bid_ticks
        stats["trade_external_price_delta_ticks"].update(price_ticks - anchor_ticks)
        stats["trade_external_latest_bid_delta_ticks"].update(latest_bid_ticks - anchor_ticks)
        stats["trade_external_latest_ask_delta_ticks"].update(latest_ask_ticks - anchor_ticks)
        stats["trade_external_latest_mid_delta_half_ticks"].update(latest_bid_ticks + latest_ask_ticks - 2 * anchor_ticks)
        stats["trade_local_price_offset_half_ticks"].update(2 * price_ticks - latest_bid_ticks - latest_ask_ticks)
        stats["trade_latest_spread_ticks"].update(latest_spread_ticks)
    else:
        anomalies.trade_invalid_count += 1


def select_block_starts(total_rows: int, context_chunks: int, row_block_size: int, max_blocks: int, rng: random.Random) -> list[int]:
    starts = list(range(context_chunks - 1, total_rows, max(row_block_size, context_chunks)))
    if max_blocks > 0 and len(starts) > max_blocks:
        starts = rng.sample(starts, max_blocks)
    return sorted(starts)


def main() -> None:
    args = parse_args()
    candidate_bits = tuple(int(value) for value in args.candidate_bits.split(",") if value.strip())
    rng = random.Random(args.seed)
    data_config = DataConfig(
        cache_root=args.chunk_root,
        train_start_date=args.start_date,
        train_end_date=args.end_date,
        validation_start_date=args.start_date,
        validation_end_date=args.end_date,
        tickers=tuple(part.strip().upper() for part in args.tickers.split(",") if part.strip()) or ("ALL",),
        chunk_ms=args.chunk_ms,
        context_seconds=args.context_seconds,
        row_block_size=args.row_block_size,
    )
    print(
        f"DISCOVER chunk files root={args.chunk_root} dates={args.start_date}->{args.end_date} "
        f"tickers={data_config.tickers} max_files={args.max_files}",
        flush=True,
    )
    files = discover_files_fast(
        args.chunk_root,
        start_date=args.start_date,
        end_date=args.end_date,
        tickers=data_config.tickers,
        chunk_ms=args.chunk_ms,
        max_files=args.max_files,
        rng=rng,
    )
    files = sorted(files, key=lambda item: (item.ticker, item.year_month))
    stats = {
        name: RunningStats(name=name, candidate_bits=candidate_bits)
        for name in (
            "quote_external_bid_delta_ticks",
            "quote_external_ask_delta_ticks",
            "quote_external_mid_delta_half_ticks",
            "quote_local_bid_offset_half_ticks",
            "quote_local_ask_offset_half_ticks",
            "quote_spread_ticks",
            "trade_external_price_delta_ticks",
            "trade_external_latest_bid_delta_ticks",
            "trade_external_latest_ask_delta_ticks",
            "trade_external_latest_mid_delta_half_ticks",
            "trade_local_price_offset_half_ticks",
            "trade_latest_spread_ticks",
        )
    }
    anomalies = AnomalyStats()
    started = time.perf_counter()
    processed_files = 0
    processed_origins = 0
    skipped_files = 0
    print(
        f"START price tick analysis files={len(files):,} root={args.chunk_root} "
        f"dates={args.start_date}->{args.end_date} context_chunks={data_config.context_chunks}",
        flush=True,
    )
    for file_index, file_info in enumerate(files, start=1):
        file_started = time.perf_counter()
        try:
            total_rows = count_chunk_rows(file_info.path, start_date=args.start_date, end_date=args.end_date)
        except Exception as exc:
            skipped_files += 1
            print(f"[{file_index}/{len(files)}] SKIP count failed {file_info.ticker}:{file_info.year_month}: {exc}", flush=True)
            continue
        if total_rows <= data_config.context_chunks:
            skipped_files += 1
            continue
        origins_left = args.max_origins_per_file
        file_origins = 0
        block_starts = select_block_starts(
            total_rows,
            data_config.context_chunks,
            args.row_block_size,
            args.max_blocks_per_file,
            rng,
        )
        for origin_start in block_starts:
            if origins_left <= 0:
                break
            origin_end = min(origin_start + args.row_block_size, total_rows)
            row_start = max(0, origin_start - data_config.context_chunks + 1)
            row_count = origin_end - row_start
            try:
                frame = load_chunk_block(
                    file_info.path,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    row_offset=row_start,
                    row_count=row_count,
                )
                arrays = frame_to_arrays(frame, data_config)
            except Exception as exc:
                print(
                    f"[{file_index}/{len(files)}] block failed {file_info.ticker}:{file_info.year_month} "
                    f"origin={origin_start:,}-{origin_end:,}: {exc}",
                    flush=True,
                )
                continue
            if arrays is None:
                continue
            local_start = origin_start - row_start
            local_end = origin_end - row_start
            origins = valid_origins(arrays, data_config)
            origins = origins[(origins >= local_start) & (origins < local_end)]
            if origins.size == 0:
                continue
            if origins.size > origins_left:
                origins = np.asarray(rng.sample(list(map(int, origins)), origins_left), dtype=np.int64)
            else:
                origins = origins.astype(np.int64, copy=False)
            for origin in origins:
                analyze_origin(arrays, int(origin), data_config.context_chunks, stats, anomalies)
            file_origins += int(origins.size)
            processed_origins += int(origins.size)
            origins_left -= int(origins.size)
        processed_files += 1
        if processed_files % max(1, args.progress_every_files) == 0 or file_index == len(files):
            elapsed = time.perf_counter() - started
            print(
                f"[{file_index}/{len(files)}] {file_info.ticker}:{file_info.year_month} "
                f"file_origins={file_origins:,} total_origins={processed_origins:,} "
                f"elapsed_min={elapsed / 60:.1f} file_sec={time.perf_counter() - file_started:.1f}",
                flush=True,
            )

    summaries = {name: stat.summary() for name, stat in stats.items()}
    recommendations = {}
    for name, summary in summaries.items():
        abs_q = summary.get("abs_quantiles", {})
        p999 = abs_q.get("p99.9_abs")
        p9999 = abs_q.get("p99.99_abs")
        recommendations[name] = {
            "bits_for_p99_9": sign_magnitude_bits_required(p999 or 0),
            "bits_for_p99_99": sign_magnitude_bits_required(p9999 or 0),
            "bits_for_max": sign_magnitude_bits_required(max(abs(summary.get("min") or 0), abs(summary.get("max") or 0))),
        }
    payload = {
        "config": {
            "chunk_root": str(args.chunk_root),
            "start_date": args.start_date,
            "end_date": args.end_date,
            "tickers": data_config.tickers,
            "chunk_ms": data_config.chunk_ms,
            "context_seconds": data_config.context_seconds,
            "context_chunks": data_config.context_chunks,
            "max_files": args.max_files,
            "max_blocks_per_file": args.max_blocks_per_file,
            "max_origins_per_file": args.max_origins_per_file,
            "row_block_size": args.row_block_size,
            "candidate_bits": candidate_bits,
        },
        "processed": {
            "files": processed_files,
            "skipped_files": skipped_files,
            "origins": processed_origins,
            "elapsed_seconds": time.perf_counter() - started,
        },
        "tick_unit_rule": {
            "anchor_mid_lt_1": 0.0001,
            "anchor_mid_ge_1": 0.01,
            "deltas_use_anchor_tick_unit": True,
        },
        "anomalies": anomalies.as_dict(),
        "stats": summaries,
        "bit_recommendations": recommendations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"DONE origins={processed_origins:,} files={processed_files:,} output={args.output}", flush=True)
    print(json.dumps({"processed": payload["processed"], "bit_recommendations": recommendations}, indent=2), flush=True)


if __name__ == "__main__":
    main()
