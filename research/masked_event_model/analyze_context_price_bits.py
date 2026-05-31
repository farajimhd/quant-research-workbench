from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pyarrow.parquet as pq

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

from research.masked_event_model.v2.schema import QUOTE_FEATURE_COLUMNS, TRADE_FEATURE_COLUMNS


DEFAULT_CHUNK_ROOT = Path(
    "//DESKTOP-SAAI85T/Workstation-D/market-data/flatfiles/us_stocks_sip/derived/event_chunks_v2"
)
DEFAULT_OUTPUT_DIR = Path("D:/TradingML/runtimes/analysis/context_price_bits")

QUOTE = {name: index for index, name in enumerate(QUOTE_FEATURE_COLUMNS)}
TRADE = {name: index for index, name in enumerate(TRADE_FEATURE_COLUMNS)}


@dataclass(slots=True)
class ChunkFile:
    ticker: str
    path: Path
    rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Empirically size compact context price bit fields.")
    parser.add_argument("--chunk-root", type=Path, default=DEFAULT_CHUNK_ROOT)
    parser.add_argument("--year-month", default="2025-11")
    parser.add_argument("--sample-chunks", type=int, default=10_000)
    parser.add_argument("--context-chunks", type=int, default=64)
    parser.add_argument("--anchor-mode", choices=("latest-ask", "median-context"), default="median-context")
    parser.add_argument("--quant-bits", type=int, default=6)
    parser.add_argument("--max-source-files", type=int, default=250)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--chunk-ms", type=int, default=500)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--progress-every-files", type=int, default=10)
    return parser.parse_args()


def layout_root(root: Path, chunk_ms: int) -> Path:
    nested = root / f"chunk_ms={chunk_ms}" / "mq=128_mt=192_m=256"
    return nested if nested.exists() else root


def discover_month_files(
    root: Path,
    *,
    year_month: str,
    chunk_ms: int,
    max_files: int,
    rng: random.Random,
) -> list[ChunkFile]:
    base = layout_root(root, chunk_ms)
    files: list[ChunkFile] = []
    ticker_dirs = [path for path in base.iterdir() if path.is_dir() and path.name.startswith("ticker=")]
    rng.shuffle(ticker_dirs)
    for ticker_dir in ticker_dirs:
        path = ticker_dir / f"{year_month}.parquet"
        if not path.exists():
            continue
        try:
            rows = int(pq.ParquetFile(str(path)).metadata.num_rows)
        except Exception:
            continue
        if rows > 0:
            files.append(ChunkFile(ticker=ticker_dir.name.split("=", 1)[1].upper(), path=path, rows=rows))
            if max_files > 0 and len(files) >= max_files:
                break
    return files


def weighted_source_files(files: list[ChunkFile], *, max_files: int, rng: random.Random) -> list[ChunkFile]:
    if max_files <= 0 or len(files) <= max_files:
        return files
    weights = np.asarray([file.rows for file in files], dtype=np.float64)
    probs = weights / weights.sum()
    selected_indices = rng_np_choice(len(files), size=max_files, replace=False, p=probs, seed=rng.randrange(1 << 32))
    return [files[int(index)] for index in sorted(selected_indices)]


def rng_np_choice(n: int, *, size: int, replace: bool, p: np.ndarray, seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    return generator.choice(n, size=size, replace=replace, p=p)


def allocate_samples(files: list[ChunkFile], *, sample_count: int, rng: random.Random) -> dict[Path, np.ndarray]:
    weights = np.asarray([file.rows for file in files], dtype=np.float64)
    probs = weights / weights.sum()
    allocations = np.random.default_rng(rng.randrange(1 << 32)).multinomial(sample_count, probs)
    out: dict[Path, np.ndarray] = {}
    for file, count in zip(files, allocations):
        if count <= 0:
            continue
        count = min(int(count), file.rows)
        indices = rng_np_choice(file.rows, size=count, replace=False, p=np.full(file.rows, 1.0 / file.rows), seed=rng.randrange(1 << 32))
        out[file.path] = np.sort(indices.astype(np.int64))
    return out


def allocate_context_samples(
    files: list[ChunkFile],
    *,
    sample_count: int,
    context_chunks: int,
    rng: random.Random,
) -> dict[Path, np.ndarray]:
    eligible = [file for file in files if file.rows >= context_chunks]
    if not eligible:
        return {}
    weights = np.asarray([max(0, file.rows - context_chunks + 1) for file in eligible], dtype=np.float64)
    probs = weights / weights.sum()
    allocations = np.random.default_rng(rng.randrange(1 << 32)).multinomial(sample_count, probs)
    out: dict[Path, np.ndarray] = {}
    for file, count in zip(eligible, allocations):
        if count <= 0:
            continue
        origin_count = max(0, file.rows - context_chunks + 1)
        count = min(int(count), origin_count)
        indices = rng_np_choice(
            origin_count,
            size=count,
            replace=False,
            p=np.full(origin_count, 1.0 / origin_count),
            seed=rng.randrange(1 << 32),
        )
        out[file.path] = np.sort(indices.astype(np.int64) + context_chunks - 1)
    return out


def tick_unit_from_price(price: float) -> float:
    return 0.0001 if price < 1.0 else 0.01


def to_ticks(values: np.ndarray, tick_unit: float) -> np.ndarray:
    return np.rint(values / tick_unit).astype(np.int64)


def finite_positive(values: np.ndarray) -> np.ndarray:
    return np.isfinite(values) & (values > 0.0)


def list_to_array(value: Any, width: int) -> np.ndarray:
    if value is None:
        return np.empty((0, width), dtype=np.float64)
    arr = np.asarray(value, dtype=np.float64)
    if arr.size == 0:
        return np.empty((0, width), dtype=np.float64)
    return arr.reshape(-1, width)


def summary(values: np.ndarray) -> tuple[int, int | None, int | None, float | None]:
    values = np.asarray(values, dtype=np.int64).reshape(-1)
    if values.size == 0:
        return 0, None, None, None
    return int(values.size), int(values.min()), int(values.max()), float(np.median(values))


def median_int(values: np.ndarray) -> int | None:
    values = np.asarray(values, dtype=np.int64).reshape(-1)
    if values.size == 0:
        return None
    return int(round(float(np.median(values))))


def signed_quant_bounds(bits: int) -> tuple[int, int]:
    if bits < 2:
        raise ValueError("--quant-bits must be at least 2 for signed context deltas")
    return -(2 ** (bits - 1)), (2 ** (bits - 1)) - 1


def quantize_delta(values: np.ndarray, *, anchor_ticks: int, bits: int) -> dict[str, int | float | None]:
    values = np.asarray(values, dtype=np.int64).reshape(-1)
    if values.size == 0:
        return {
            "count": 0,
            "scale_ticks": None,
            "code_min": None,
            "code_max": None,
            "error_max": None,
            "error_mean": None,
            "error_p95": None,
            "error_p99": None,
            "lossless": None,
            "clipped_count": 0,
        }
    code_min, code_max = signed_quant_bounds(bits)
    deltas = values - int(anchor_ticks)
    negative_span = abs(int(deltas.min()))
    positive_span = abs(int(deltas.max()))
    scale_ticks = max(
        1,
        int(math.ceil(negative_span / abs(code_min))) if negative_span else 1,
        int(math.ceil(positive_span / code_max)) if positive_span else 1,
    )
    codes_unclipped = np.rint(deltas / float(scale_ticks)).astype(np.int64)
    codes = np.clip(codes_unclipped, code_min, code_max)
    reconstructed = int(anchor_ticks) + codes * scale_ticks
    errors = np.abs(values - reconstructed)
    clipped = codes != codes_unclipped
    return {
        "count": int(values.size),
        "scale_ticks": int(scale_ticks),
        "code_min": int(codes.min()),
        "code_max": int(codes.max()),
        "error_max": int(errors.max()),
        "error_mean": float(errors.mean()),
        "error_p95": float(np.percentile(errors, 95)),
        "error_p99": float(np.percentile(errors, 99)),
        "lossless": int(errors.max() == 0 and not clipped.any()),
        "clipped_count": int(clipped.sum()),
    }


def analyze_chunk(row: dict[str, Any]) -> dict[str, Any]:
    anchor = row.get("latest_ask")
    if anchor is None or not math.isfinite(float(anchor)) or float(anchor) <= 0.0:
        quote_values = list_to_array(row.get("quote_values"), len(QUOTE_FEATURE_COLUMNS))
        if quote_values.size:
            asks = quote_values[:, QUOTE["ask_price"]]
            valid_asks = asks[finite_positive(asks)]
            anchor = float(valid_asks[-1]) if valid_asks.size else float("nan")
        else:
            anchor = float("nan")
    anchor = float(anchor)
    result: dict[str, Any] = {
        "ticker": row.get("ticker"),
        "session_date": row.get("session_date"),
        "chunk_start_ns": row.get("chunk_start_ns"),
        "chunk_end_ns": row.get("chunk_end_ns"),
        "anchor_ask": anchor,
    }
    if not math.isfinite(anchor) or anchor <= 0.0:
        result["valid_anchor"] = 0
        return result
    tick_unit = tick_unit_from_price(anchor)
    anchor_ticks = int(round(anchor / tick_unit))
    result.update(
        {
            "valid_anchor": 1,
            "tick_unit": tick_unit,
            "anchor_ticks": anchor_ticks,
            "anchor_tick_regime": "sub_dollar" if tick_unit < 0.01 else "penny",
        }
    )

    quote_values = list_to_array(row.get("quote_values"), len(QUOTE_FEATURE_COLUMNS))
    if quote_values.size:
        bid = quote_values[:, QUOTE["bid_price"]]
        ask = quote_values[:, QUOTE["ask_price"]]
        valid = finite_positive(bid) & finite_positive(ask) & (ask >= bid)
        ask_ticks = to_ticks(ask[valid], tick_unit)
        bid_ticks = to_ticks(bid[valid], tick_unit)
        quote_ask_delta = ask_ticks - anchor_ticks
        quote_spread = ask_ticks - bid_ticks
    else:
        quote_ask_delta = np.empty(0, dtype=np.int64)
        quote_spread = np.empty(0, dtype=np.int64)

    trade_values = list_to_array(row.get("trade_values"), len(TRADE_FEATURE_COLUMNS))
    if trade_values.size:
        trade_price = trade_values[:, TRADE["price"]]
        valid_trade = finite_positive(trade_price)
        trade_ticks = to_ticks(trade_price[valid_trade], tick_unit)
        trade_delta = trade_ticks - anchor_ticks
    else:
        trade_delta = np.empty(0, dtype=np.int64)

    for prefix, values in (
        ("quote_ask_delta", quote_ask_delta),
        ("quote_spread", quote_spread),
        ("trade_delta", trade_delta),
    ):
        count, minimum, maximum, median = summary(values)
        result[f"{prefix}_count"] = count
        result[f"{prefix}_min"] = minimum
        result[f"{prefix}_max"] = maximum
        result[f"{prefix}_median"] = median
    return result


def extract_context_prices(context_rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    quote_asks: list[np.ndarray] = []
    quote_bids: list[np.ndarray] = []
    trade_prices: list[np.ndarray] = []
    trade_latest_asks: list[np.ndarray] = []
    trade_latest_bids: list[np.ndarray] = []

    for row in context_rows:
        quote_values = list_to_array(row.get("quote_values"), len(QUOTE_FEATURE_COLUMNS))
        if quote_values.size:
            bid = quote_values[:, QUOTE["bid_price"]]
            ask = quote_values[:, QUOTE["ask_price"]]
            valid = finite_positive(bid) & finite_positive(ask) & (ask >= bid)
            if valid.any():
                quote_bids.append(bid[valid])
                quote_asks.append(ask[valid])

        trade_values = list_to_array(row.get("trade_values"), len(TRADE_FEATURE_COLUMNS))
        if trade_values.size:
            price = trade_values[:, TRADE["price"]]
            valid_trade = finite_positive(price)
            if valid_trade.any():
                trade_prices.append(price[valid_trade])
            latest_bid = trade_values[:, TRADE["latest_bid"]]
            latest_ask = trade_values[:, TRADE["latest_ask"]]
            valid_trade_quote = finite_positive(latest_bid) & finite_positive(latest_ask) & (latest_ask >= latest_bid)
            if valid_trade_quote.any():
                trade_latest_bids.append(latest_bid[valid_trade_quote])
                trade_latest_asks.append(latest_ask[valid_trade_quote])

    def concat(parts: list[np.ndarray]) -> np.ndarray:
        return np.concatenate(parts).astype(np.float64) if parts else np.empty(0, dtype=np.float64)

    return {
        "quote_bids": concat(quote_bids),
        "quote_asks": concat(quote_asks),
        "trade_prices": concat(trade_prices),
        "trade_latest_bids": concat(trade_latest_bids),
        "trade_latest_asks": concat(trade_latest_asks),
    }


def analyze_context(context_rows: list[dict[str, Any]], *, origin_index: int, quant_bits: int) -> dict[str, Any]:
    origin = context_rows[-1]
    prices = extract_context_prices(context_rows)
    anchor_candidates = prices["quote_asks"]
    if anchor_candidates.size == 0:
        anchor_candidates = prices["trade_latest_asks"]
    if anchor_candidates.size == 0:
        anchor = float("nan")
    else:
        anchor = float(np.median(anchor_candidates))

    result: dict[str, Any] = {
        "ticker": origin.get("ticker"),
        "session_date": origin.get("session_date"),
        "origin_row_index": origin_index,
        "chunk_start_ns": origin.get("chunk_start_ns"),
        "chunk_end_ns": origin.get("chunk_end_ns"),
        "anchor_ask": anchor,
    }
    if not math.isfinite(anchor) or anchor <= 0.0:
        result["valid_anchor"] = 0
        return result

    tick_unit = tick_unit_from_price(anchor)
    anchor_ticks = int(round(anchor / tick_unit))
    quote_asks = prices["quote_asks"]
    quote_bids = prices["quote_bids"]
    trade_prices = prices["trade_prices"]
    quote_ask_delta = np.empty(0, dtype=np.int64)
    quote_spread = np.empty(0, dtype=np.int64)
    quote_spread_delta = np.empty(0, dtype=np.int64)
    spread_anchor_ticks: int | None = None

    if quote_asks.size:
        ask_ticks = to_ticks(quote_asks, tick_unit)
        bid_ticks = to_ticks(quote_bids, tick_unit)
        quote_ask_delta = ask_ticks - anchor_ticks
        quote_spread = ask_ticks - bid_ticks
        spread_anchor_ticks = median_int(quote_spread)
        quote_spread_delta = quote_spread - int(spread_anchor_ticks)
    else:
        trade_latest_asks = prices["trade_latest_asks"]
        trade_latest_bids = prices["trade_latest_bids"]
        if trade_latest_asks.size:
            spread_ticks = to_ticks(trade_latest_asks, tick_unit) - to_ticks(trade_latest_bids, tick_unit)
            spread_anchor_ticks = median_int(spread_ticks)

    if trade_prices.size:
        trade_delta = to_ticks(trade_prices, tick_unit) - anchor_ticks
    else:
        trade_delta = np.empty(0, dtype=np.int64)

    if spread_anchor_ticks is None:
        spread_anchor_ticks = 0

    result.update(
        {
            "valid_anchor": 1,
            "tick_unit": tick_unit,
            "anchor_ticks": anchor_ticks,
            "spread_anchor_ticks": int(spread_anchor_ticks),
            "anchor_tick_regime": "sub_dollar" if tick_unit < 0.01 else "penny",
        }
    )
    for prefix, values in (
        ("quote_ask_delta", quote_ask_delta),
        ("quote_spread", quote_spread),
        ("quote_spread_delta", quote_spread_delta),
        ("trade_delta", trade_delta),
    ):
        count, minimum, maximum, median = summary(values)
        result[f"{prefix}_count"] = count
        result[f"{prefix}_min"] = minimum
        result[f"{prefix}_max"] = maximum
        result[f"{prefix}_median"] = median
    quant_inputs = {
        "quote_ask": (to_ticks(quote_asks, tick_unit) if quote_asks.size else np.empty(0, dtype=np.int64), anchor_ticks),
        "quote_spread": (quote_spread, int(spread_anchor_ticks)),
        "trade_price": (to_ticks(trade_prices, tick_unit) if trade_prices.size else np.empty(0, dtype=np.int64), anchor_ticks),
    }
    for prefix, (values, quant_anchor) in quant_inputs.items():
        quant = quantize_delta(values, anchor_ticks=quant_anchor, bits=quant_bits)
        for key, value in quant.items():
            result[f"{prefix}_q{quant_bits}_{key}"] = value
    return result


def read_sampled_rows(path: Path, indices: np.ndarray) -> list[dict[str, Any]]:
    if indices.size == 0:
        return []
    frame = (
        pl.scan_parquet(str(path))
        .with_row_index("_row_nr")
        .filter(pl.col("_row_nr").is_in([int(value) for value in indices]))
        .select(
            [
                "ticker",
                "session_date",
                "chunk_start_ns",
                "chunk_end_ns",
                "latest_ask",
                "quote_values",
                "trade_values",
            ]
        )
        .collect()
    )
    return frame.to_dicts()


def read_sampled_contexts(path: Path, origin_indices: np.ndarray, *, context_chunks: int) -> list[tuple[int, list[dict[str, Any]]]]:
    if origin_indices.size == 0:
        return []
    needed: set[int] = set()
    for origin_index in origin_indices:
        start = int(origin_index) - context_chunks + 1
        if start < 0:
            continue
        needed.update(range(start, int(origin_index) + 1))
    if not needed:
        return []
    frame = (
        pl.scan_parquet(str(path))
        .with_row_index("_row_nr")
        .filter(pl.col("_row_nr").is_in(sorted(needed)))
        .select(
            [
                "_row_nr",
                "ticker",
                "session_date",
                "chunk_start_ns",
                "chunk_end_ns",
                "latest_ask",
                "quote_values",
                "trade_values",
            ]
        )
        .collect()
        .sort("_row_nr")
    )
    by_index = {int(row["_row_nr"]): row for row in frame.to_dicts()}
    contexts: list[tuple[int, list[dict[str, Any]]]] = []
    for origin_index in origin_indices:
        origin_index = int(origin_index)
        start = origin_index - context_chunks + 1
        rows = [by_index[index] for index in range(start, origin_index + 1) if index in by_index]
        if len(rows) == context_chunks:
            contexts.append((origin_index, rows))
    return contexts


def bits_for_unsigned(maximum: int) -> int:
    if maximum <= 0:
        return 0
    return int(math.ceil(math.log2(maximum + 1)))


def bits_for_signed(minimum: int, maximum: int) -> dict[str, int]:
    max_abs = max(abs(int(minimum)), abs(int(maximum)))
    magnitude_bits = bits_for_unsigned(max_abs)
    return {"sign_bits": 1 if max_abs > 0 else 0, "magnitude_bits": magnitude_bits, "total_bits": (1 if max_abs > 0 else 0) + magnitude_bits}


def value_quantiles(values: list[int]) -> dict[str, float] | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=np.float64)
    return {f"p{q}": float(np.percentile(arr, q)) for q in (0, 1, 5, 50, 95, 99, 99.9, 100)}


def float_quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=np.float64)
    return {f"p{q}": float(np.percentile(arr, q)) for q in (0, 1, 5, 50, 95, 99, 99.9, 100)}


def column_values(rows: list[dict[str, Any]], column: str) -> list[int]:
    values: list[int] = []
    for row in rows:
        value = row.get(column)
        if value is not None and isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(int(value))
    return values


def column_floats(rows: list[dict[str, Any]], column: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(column)
        if value is not None and isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def build_summary(rows: list[dict[str, Any]], *, elapsed_seconds: float, config: dict[str, Any]) -> dict[str, Any]:
    valid_rows = [row for row in rows if row.get("valid_anchor") == 1]
    quant_bits = int(config.get("quant_bits", 6))
    price_fields = {}
    for name, signed in (
        ("quote_ask_delta", True),
        ("quote_spread", False),
        ("quote_spread_delta", True),
        ("trade_delta", True),
    ):
        mins = column_values(valid_rows, f"{name}_min")
        maxs = column_values(valid_rows, f"{name}_max")
        medians = column_values(valid_rows, f"{name}_median")
        if not mins and not maxs:
            continue
        global_min = min(mins) if mins else 0
        global_max = max(maxs) if maxs else 0
        if signed:
            bit_width = bits_for_signed(global_min, global_max)
        else:
            bit_width = {"magnitude_bits": bits_for_unsigned(global_max), "total_bits": bits_for_unsigned(global_max)}
        price_fields[name] = {
            "global_min": global_min,
            "global_max": global_max,
            "chunk_min_quantiles": value_quantiles(mins),
            "chunk_max_quantiles": value_quantiles(maxs),
            "chunk_median_quantiles": value_quantiles([int(round(value)) for value in medians]),
            "lossless_bits": bit_width,
        }
    anchor_ticks = column_values(valid_rows, "anchor_ticks")
    spread_anchor_ticks = column_values(valid_rows, "spread_anchor_ticks")
    anchor_bits = bits_for_unsigned(max(anchor_ticks)) if anchor_ticks else 0
    spread_anchor_bits = bits_for_unsigned(max(spread_anchor_ticks)) if spread_anchor_ticks else 0
    spread_delta_bits = price_fields.get("quote_spread_delta", {}).get("lossless_bits", {}).get("total_bits")
    spread_bits = price_fields.get("quote_spread", {}).get("lossless_bits", {}).get("total_bits", 0)
    per_chunk_lossless_bits = {
        "anchor_ticks_unsigned_bits": anchor_bits,
        "tick_regime_bits": 1,
        "quote_ask_delta_bits": price_fields.get("quote_ask_delta", {}).get("lossless_bits", {}).get("total_bits", 0),
        "spread_anchor_unsigned_bits": spread_anchor_bits,
        "quote_spread_delta_bits": spread_delta_bits or 0,
        "quote_spread_bits": spread_bits,
        "trade_delta_bits": price_fields.get("trade_delta", {}).get("lossless_bits", {}).get("total_bits", 0),
    }
    per_chunk_lossless_bits["total_for_one_triplet_without_validity"] = (
        per_chunk_lossless_bits["quote_ask_delta_bits"]
        + (per_chunk_lossless_bits["quote_spread_delta_bits"] or per_chunk_lossless_bits["quote_spread_bits"])
        + per_chunk_lossless_bits["trade_delta_bits"]
    )
    per_chunk_lossless_bits["context_prefix_bits"] = (
        per_chunk_lossless_bits["anchor_ticks_unsigned_bits"]
        + per_chunk_lossless_bits["tick_regime_bits"]
        + per_chunk_lossless_bits["spread_anchor_unsigned_bits"]
    )
    per_chunk_lossless_bits["context_prefix_plus_one_triplet_bits"] = (
        per_chunk_lossless_bits["context_prefix_bits"]
        + per_chunk_lossless_bits["total_for_one_triplet_without_validity"]
    )
    quantized_fields = {}
    for name in ("quote_ask", "quote_spread", "trade_price"):
        prefix = f"{name}_q{quant_bits}"
        counts = column_values(valid_rows, f"{prefix}_count")
        scales = column_values(valid_rows, f"{prefix}_scale_ticks")
        error_max = column_values(valid_rows, f"{prefix}_error_max")
        error_mean = column_floats(valid_rows, f"{prefix}_error_mean")
        error_p95 = column_floats(valid_rows, f"{prefix}_error_p95")
        error_p99 = column_floats(valid_rows, f"{prefix}_error_p99")
        lossless_flags = column_values(valid_rows, f"{prefix}_lossless")
        clipped_counts = column_values(valid_rows, f"{prefix}_clipped_count")
        active_contexts = sum(1 for value in counts if value > 0)
        quantized_fields[name] = {
            "bits_per_code": quant_bits,
            "active_contexts": active_contexts,
            "lossless_contexts": int(sum(lossless_flags)),
            "lossless_context_rate": float(sum(lossless_flags) / active_contexts) if active_contexts else None,
            "scale_ticks_quantiles": value_quantiles(scales),
            "scale_ticks_unsigned_lossless_bits": bits_for_unsigned(max(scales)) if scales else 0,
            "error_max_tick_quantiles": value_quantiles(error_max),
            "error_mean_tick_quantiles": float_quantiles(error_mean),
            "error_p95_tick_quantiles": float_quantiles(error_p95),
            "error_p99_tick_quantiles": float_quantiles(error_p99),
            "contexts_with_clipping": int(sum(1 for value in clipped_counts if value > 0)),
            "total_clipped_values": int(sum(clipped_counts)),
        }
    scale_bits = {
        name: metrics["scale_ticks_unsigned_lossless_bits"]
        for name, metrics in quantized_fields.items()
    }
    quantized_bits = {
        "ask_anchor_ticks_unsigned_bits": anchor_bits,
        "tick_regime_bits": 1,
        "spread_anchor_unsigned_bits": spread_anchor_bits,
        "quote_ask_scale_unsigned_bits": scale_bits.get("quote_ask", 0),
        "quote_spread_scale_unsigned_bits": scale_bits.get("quote_spread", 0),
        "trade_price_scale_unsigned_bits": scale_bits.get("trade_price", 0),
        "context_prefix_bits": anchor_bits
        + 1
        + spread_anchor_bits
        + scale_bits.get("quote_ask", 0)
        + scale_bits.get("quote_spread", 0)
        + scale_bits.get("trade_price", 0),
        "quote_ask_code_bits": quant_bits,
        "quote_spread_code_bits": quant_bits,
        "trade_price_code_bits": quant_bits,
        "per_triplet_code_bits": quant_bits * 3,
    }
    context_chunks = int(config.get("context_chunks", 64))
    quantized_bits[f"context_prefix_plus_{context_chunks}_triplets_bits"] = quantized_bits["context_prefix_bits"] + (
        context_chunks * quantized_bits["per_triplet_code_bits"]
    )
    quantized_bits[f"float16_{context_chunks}_triplets_bits"] = context_chunks * 3 * 16
    return {
        "config": config,
        "processed": {
            "sampled_rows": len(rows),
            "valid_anchor_rows": len(valid_rows),
            "elapsed_seconds": elapsed_seconds,
        },
        "anchor": {
            "anchor_ticks_min": min(anchor_ticks) if anchor_ticks else None,
            "anchor_ticks_max": max(anchor_ticks) if anchor_ticks else None,
            "anchor_ticks_unsigned_lossless_bits": anchor_bits,
            "spread_anchor_ticks_min": min(spread_anchor_ticks) if spread_anchor_ticks else None,
            "spread_anchor_ticks_max": max(spread_anchor_ticks) if spread_anchor_ticks else None,
            "spread_anchor_ticks_unsigned_lossless_bits": spread_anchor_bits,
            "tick_regime_counts": {
                "penny": sum(1 for row in valid_rows if row.get("anchor_tick_regime") == "penny"),
                "sub_dollar": sum(1 for row in valid_rows if row.get("anchor_tick_regime") == "sub_dollar"),
            },
        },
        "price_fields": price_fields,
        "quantized_fields": quantized_fields,
        "lossless_bits": per_chunk_lossless_bits,
        "quantized_bits": quantized_bits,
        "float16_baseline_bits_per_chunk_triplet": 3 * 16,
    }


def write_rows_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row.keys()})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    rng = random.Random(args.seed)
    print(
        f"DISCOVER files root={args.chunk_root} month={args.year_month} sample_chunks={args.sample_chunks:,}",
        flush=True,
    )
    files = discover_month_files(
        args.chunk_root,
        year_month=args.year_month,
        chunk_ms=args.chunk_ms,
        max_files=args.max_source_files,
        rng=rng,
    )
    if not files:
        raise FileNotFoundError(f"No {args.year_month} parquet files found under {layout_root(args.chunk_root, args.chunk_ms)}")
    source_files = weighted_source_files(files, max_files=args.max_source_files, rng=rng)
    if args.anchor_mode == "median-context":
        allocations = allocate_context_samples(
            source_files,
            sample_count=args.sample_chunks,
            context_chunks=args.context_chunks,
            rng=rng,
        )
    else:
        allocations = allocate_samples(source_files, sample_count=args.sample_chunks, rng=rng)
    rows: list[dict[str, Any]] = []
    print(f"START source_files={len(source_files):,} allocated_files={len(allocations):,}", flush=True)
    for file_index, file in enumerate(source_files, start=1):
        indices = allocations.get(file.path)
        if indices is None or indices.size == 0:
            continue
        file_started = time.perf_counter()
        try:
            if args.anchor_mode == "median-context":
                sampled_contexts = read_sampled_contexts(file.path, indices, context_chunks=args.context_chunks)
                rows.extend(analyze_context(context_rows, origin_index=origin_index, quant_bits=args.quant_bits) for origin_index, context_rows in sampled_contexts)
            else:
                sampled_rows = read_sampled_rows(file.path, indices)
                rows.extend(analyze_chunk(row) for row in sampled_rows)
        except Exception as exc:
            print(f"[{file_index}/{len(source_files)}] FAILED {file.ticker} rows={indices.size}: {exc}", flush=True)
            continue
        if file_index % max(1, args.progress_every_files) == 0 or len(rows) >= args.sample_chunks:
            print(
                f"[{file_index}/{len(source_files)}] {file.ticker} sampled_rows={len(rows):,}/{args.sample_chunks:,} "
                f"file_sec={time.perf_counter() - file_started:.1f} elapsed_min={(time.perf_counter() - started) / 60:.1f}",
                flush=True,
            )
    rows = rows[: args.sample_chunks]
    config = {
        "chunk_root": str(args.chunk_root),
        "year_month": args.year_month,
        "sample_chunks": args.sample_chunks,
        "max_source_files": args.max_source_files,
        "seed": args.seed,
        "chunk_ms": args.chunk_ms,
        "context_chunks": args.context_chunks,
        "anchor_mode": args.anchor_mode,
        "quant_bits": args.quant_bits,
        "anchor": "median context ask" if args.anchor_mode == "median-context" else "latest_ask",
        "spread_anchor": "median context quote spread" if args.anchor_mode == "median-context" else None,
        "tick_rule": "anchor >= 1 uses 0.01, anchor < 1 uses 0.0001; all deltas use anchor tick unit",
        "price_fields": ["quote_ask_delta", "quote_spread_delta", "trade_delta"],
    }
    report = build_summary(rows, elapsed_seconds=time.perf_counter() - started, config=config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.anchor_mode.replace('-', '_')}_ctx{args.context_chunks}_q{args.quant_bits}_{args.year_month}_{args.sample_chunks}"
    rows_path = args.output_dir / f"context_price_bits_rows_{suffix}.csv"
    report_path = args.output_dir / f"context_price_bits_report_{suffix}.json"
    write_rows_csv(rows_path, rows)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"DONE rows={len(rows):,} report={report_path} rows_csv={rows_path}", flush=True)
    print(json.dumps({"lossless_bits": report["lossless_bits"], "quantized_bits": report["quantized_bits"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
