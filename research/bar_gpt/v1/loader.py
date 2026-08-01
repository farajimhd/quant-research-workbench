from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Mapping
from urllib import error, parse, request

import numpy as np
import polars as pl
import torch

from research.bar_gpt.v1.data import BarView, densify_one_second_view
from research.bar_gpt.v1.features import project_stationary_features
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.mlops.clickhouse import quote_ident, sql_string


@dataclass(frozen=True, slots=True)
class ClickHouseBarStreamConfig:
    url: str
    user: str
    password: str
    database: str = "market_sip_compact"
    table: str = "bar_gpt_1s_bars_v1"
    max_threads: int = 4
    max_block_size: int = 65_536
    max_memory_usage: int = 8 * 1024**3


def ticker_range_query(
    config: ClickHouseBarStreamConfig,
    *,
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    columns = ",\n    ".join(
        (
            "local_date",
            "ticker",
            "bar_start_us",
            "bar_end_us",
            "available_at_us",
            *FEATURE_NAMES,
        )
    )
    return f"""
SELECT
    {columns}
FROM {quote_ident(config.database)}.{quote_ident(config.table)}
PREWHERE ticker = {sql_string(ticker.upper())}
  AND local_date >= toDate({sql_string(start_date)})
  AND local_date < toDate({sql_string(end_date)})
ORDER BY ticker, local_date, bucket_index
SETTINGS
    max_threads = {max(1, int(config.max_threads))},
    max_block_size = {max(1, int(config.max_block_size))},
    max_memory_usage = {max(1, int(config.max_memory_usage))}
FORMAT ArrowStream
"""


def certified_ranges_query(database: str, manifest_table: str, target_table: str) -> str:
    return f"""
SELECT
    local_date AS partition_month,
    unit_id,
    message,
    output_row_count
FROM {quote_ident(database)}.{quote_ident(manifest_table)} FINAL
WHERE artifact_name = {sql_string(target_table)}
  AND status = 'certified_range'
ORDER BY local_date, unit_id
FORMAT JSONEachRow
"""


def daily_range_query(
    config: ClickHouseBarStreamConfig,
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    daily_table: str = "macro_bars_by_time_symbol",
) -> str:
    return f"""
SELECT
    session_date AS local_date,
    sym AS ticker,
    toString(bar_family) AS bar_family,
    toUnixTimestamp64Micro(bar_start) AS bar_start_us,
    toUnixTimestamp64Micro(bar_end) AS bar_end_us,
    open,
    high,
    low,
    close,
    size_sum,
    size_open,
    size_high,
    size_low,
    size_close,
    event_count
FROM {quote_ident(config.database)}.{quote_ident(daily_table)} FINAL
PREWHERE timeframe = '1d'
  AND sym = {sql_string(ticker.upper())}
WHERE session_date >= toDate({sql_string(start_date)})
  AND session_date < toDate({sql_string(end_date)})
ORDER BY sym, bar_start, bar_family
SETTINGS max_threads = {max(1, int(config.max_threads))}, max_block_size = {max(1, int(config.max_block_size))}
FORMAT ArrowStream
"""


class ArrowStreamClient:
    """Incremental ClickHouse ArrowStream reader; response bodies are never read_all()."""

    def __init__(self, config: ClickHouseBarStreamConfig) -> None:
        self.config = config

    @contextmanager
    def record_batches(self, sql: str, *, query_id: str | None = None):
        query = sql.strip().rstrip(";")
        if not re.search(r"\bFORMAT\s+ArrowStream\s*$", query, flags=re.IGNORECASE):
            raise ValueError("ArrowStreamClient requires FORMAT ArrowStream")
        identifier = query_id or f"bar_gpt_arrow_{uuid.uuid4().hex}"
        url = self.config.url.rstrip("/") + "/?" + parse.urlencode({"query_id": identifier})
        req = request.Request(url, data=query.encode("utf-8"), method="POST")
        if self.config.user:
            req.add_header("X-ClickHouse-User", self.config.user)
        if self.config.password:
            req.add_header("X-ClickHouse-Key", self.config.password)
        try:
            response = request.urlopen(req, timeout=None)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc
        try:
            import pyarrow as pa

            reader = pa.ipc.open_stream(response)
            yield reader
        finally:
            response.close()

    def iter_session_views(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        device: torch.device | str = "cpu",
    ) -> Iterator[tuple[str, BarView]]:
        query = ticker_range_query(self.config, ticker=ticker, start_date=start_date, end_date=end_date)
        current_date = ""
        current_frames: list[pl.DataFrame] = []
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                if frame.is_empty():
                    continue
                for part in frame.partition_by("local_date", maintain_order=True):
                    date_value = str(part["local_date"][0])
                    if current_date and date_value != current_date:
                        yield current_date, frame_to_dense_view(pl.concat(current_frames, how="vertical"), device=device)
                        current_frames = []
                    current_date = date_value
                    current_frames.append(part)
        if current_frames:
            yield current_date, frame_to_dense_view(pl.concat(current_frames, how="vertical"), device=device)


def frame_to_dense_view(frame: pl.DataFrame, *, device: torch.device | str = "cpu") -> BarView:
    if frame.is_empty():
        raise ValueError("cannot convert an empty bar frame")
    if frame["ticker"].n_unique() != 1 or frame["local_date"].n_unique() != 1:
        raise ValueError("frame_to_dense_view requires one ticker/session")
    frame = frame.sort("bar_start_us")
    feature_array = frame.select(list(FEATURE_NAMES)).to_numpy().astype("float32", copy=False)
    sparse = BarView(
        features=torch.as_tensor(feature_array, device=device),
        bar_start_us=torch.as_tensor(frame["bar_start_us"].to_numpy(), dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(frame["bar_end_us"].to_numpy(), dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(frame["available_at_us"].to_numpy(), dtype=torch.long, device=device),
    )
    return densify_one_second_view(sparse)


def daily_family_frame_to_view(
    frame: pl.DataFrame,
    *,
    device: torch.device | str = "cpu",
) -> tuple[list[str], BarView]:
    """Pivot completed 1d trade/bid/ask rows into the shared wide feature contract."""
    if frame.is_empty() or frame["ticker"].n_unique() != 1:
        raise ValueError("a non-empty single-ticker daily frame is required")
    frame = frame.sort(["bar_start_us", "bar_family"])
    starts = np.unique(frame["bar_start_us"].to_numpy())
    positions = np.searchsorted(starts, frame["bar_start_us"].to_numpy())
    values = np.zeros((starts.shape[0], len(FEATURE_NAMES)), dtype=np.float32)
    family_prefix = {"trade": "trade", "quote_bid": "bid", "quote_ask": "ask"}
    event_counts = {family: np.zeros(starts.shape[0], dtype=np.float64) for family in family_prefix}
    for row_index, family_value in enumerate(frame["bar_family"].to_list()):
        family = str(family_value)
        prefix = family_prefix.get(family)
        if prefix is None:
            continue
        output_index = int(positions[row_index])
        values[output_index, FEATURE_INDEX[f"{prefix}_present"]] = 1.0
        for field in ("open", "high", "low", "close", "size_sum", "size_open", "size_high", "size_low", "size_close", "event_count"):
            values[output_index, FEATURE_INDEX[f"{prefix}_{field}"]] = float(frame[field][row_index])
        event_counts[family][output_index] = float(frame["event_count"][row_index])
    values[:, FEATURE_INDEX["source_event_count"]] = event_counts["trade"] + np.maximum(
        event_counts["quote_bid"], event_counts["quote_ask"]
    )
    grouped = frame.group_by("bar_start_us", maintain_order=True).agg(
        pl.col("local_date").first(),
        pl.col("bar_end_us").max(),
    )
    ends = grouped["bar_end_us"].to_numpy()
    dates = [str(value) for value in grouped["local_date"].to_list()]
    return dates, BarView(
        features=torch.as_tensor(values, device=device),
        bar_start_us=torch.as_tensor(starts, dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(ends, dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(ends, dtype=torch.long, device=device),
    )


def calendar_period_ids(dates: list[str], timeframe: str, *, device: torch.device | str = "cpu") -> torch.Tensor:
    import datetime as dt

    parsed = [dt.date.fromisoformat(value) for value in dates]
    if timeframe == "1W":
        ids = [day.isocalendar().year * 100 + day.isocalendar().week for day in parsed]
    elif timeframe == "1MO":
        ids = [day.year * 100 + day.month for day in parsed]
    else:
        raise ValueError("calendar timeframe must be 1W or 1MO")
    return torch.as_tensor(ids, dtype=torch.long, device=device)


def loader_contract() -> Mapping[str, object]:
    return {
        "format": "ArrowStream incremental record batches",
        "ordering": ("ticker", "local_date", "bucket_index"),
        "storage_rows": "sparse active 1s buckets",
        "model_clock": "dense 1s with explicit family availability masks",
        "coarse_views": "loader-side segment reductions",
    }


def model_features(view: BarView) -> torch.Tensor:
    return project_stationary_features(view.features)
