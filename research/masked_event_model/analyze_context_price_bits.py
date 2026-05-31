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


def column_values(rows: list[dict[str, Any]], column: str) -> list[int]:
    values: list[int] = []
    for row in rows:
        value = row.get(column)
        if value is not None and isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(int(value))
    return values


def build_summary(rows: list[dict[str, Any]], *, elapsed_seconds: float, config: dict[str, Any]) -> dict[str, Any]:
    valid_rows = [row for row in rows if row.get("valid_anchor") == 1]
    price_fields = {}
    for name, signed in (
        ("quote_ask_delta", True),
        ("quote_spread", False),
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
    anchor_bits = bits_for_unsigned(max(anchor_ticks)) if anchor_ticks else 0
    per_chunk_lossless_bits = {
        "anchor_ticks_unsigned_bits": anchor_bits,
        "tick_regime_bits": 1,
        "quote_ask_delta_bits": price_fields.get("quote_ask_delta", {}).get("lossless_bits", {}).get("total_bits", 0),
        "quote_spread_bits": price_fields.get("quote_spread", {}).get("lossless_bits", {}).get("total_bits", 0),
        "trade_delta_bits": price_fields.get("trade_delta", {}).get("lossless_bits", {}).get("total_bits", 0),
    }
    per_chunk_lossless_bits["total_for_one_triplet_without_validity"] = (
        per_chunk_lossless_bits["quote_ask_delta_bits"]
        + per_chunk_lossless_bits["quote_spread_bits"]
        + per_chunk_lossless_bits["trade_delta_bits"]
    )
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
            "tick_regime_counts": {
                "penny": sum(1 for row in valid_rows if row.get("anchor_tick_regime") == "penny"),
                "sub_dollar": sum(1 for row in valid_rows if row.get("anchor_tick_regime") == "sub_dollar"),
            },
        },
        "price_fields": price_fields,
        "lossless_bits": per_chunk_lossless_bits,
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
    allocations = allocate_samples(source_files, sample_count=args.sample_chunks, rng=rng)
    rows: list[dict[str, Any]] = []
    print(f"START source_files={len(source_files):,} allocated_files={len(allocations):,}", flush=True)
    for file_index, file in enumerate(source_files, start=1):
        indices = allocations.get(file.path)
        if indices is None or indices.size == 0:
            continue
        file_started = time.perf_counter()
        try:
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
        "anchor": "latest_ask",
        "tick_rule": "anchor >= 1 uses 0.01, anchor < 1 uses 0.0001; all deltas use anchor tick unit",
        "price_fields": ["quote_ask_delta", "quote_spread", "trade_delta"],
    }
    report = build_summary(rows, elapsed_seconds=time.perf_counter() - started, config=config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = args.output_dir / f"context_price_bits_rows_{args.year_month}_{args.sample_chunks}.csv"
    report_path = args.output_dir / f"context_price_bits_report_{args.year_month}_{args.sample_chunks}.json"
    write_rows_csv(rows_path, rows)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"DONE rows={len(rows):,} report={report_path} rows_csv={rows_path}", flush=True)
    print(json.dumps(report["lossless_bits"], indent=2), flush=True)


if __name__ == "__main__":
    main()
