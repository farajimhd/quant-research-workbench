from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Iterator, Mapping
from urllib import error, parse, request
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import torch
from torch.utils.data import DataLoader, IterableDataset, get_worker_info

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.data import (
    PATHWAY_ID_BY_NAME,
    TIMEFRAME_US_BY_NAME,
    BarGPTExample,
    BarView,
    causal_asof_indices,
    collate_examples,
    densify_one_second_view,
    rollup_calendar_view,
    rollup_intraday_view,
)
from research.bar_gpt.v1.features import project_stationary_features
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES, SESSION_END_SECOND, SESSION_START_SECOND, SESSION_TIMEZONE
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


def daily_tickers_range_query(
    config: ClickHouseBarStreamConfig,
    *,
    tickers: tuple[str, ...],
    start_date: str,
    end_date: str,
    daily_table: str = "macro_bars_by_time_symbol",
) -> str:
    if not tickers:
        raise ValueError("at least one daily ticker is required")
    selected = ", ".join(sql_string(ticker.upper()) for ticker in tickers)
    return f"""
SELECT
    session_date AS local_date,
    sym AS ticker,
    toString(bar_family) AS bar_family,
    toUnixTimestamp64Micro(bar_start) AS bar_start_us,
    toUnixTimestamp64Micro(bar_end) AS bar_end_us,
    open, high, low, close,
    size_sum, size_open, size_high, size_low, size_close,
    event_count
FROM {quote_ident(config.database)}.{quote_ident(daily_table)} FINAL
PREWHERE timeframe = '1d'
  AND session_date >= toDate({sql_string(start_date)})
  AND session_date < toDate({sql_string(end_date)})
WHERE sym IN ({selected})
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

    def read_daily_view(
        self,
        *,
        ticker: str,
        start_date: str,
        end_date: str,
        daily_table: str,
        device: torch.device | str = "cpu",
    ) -> tuple[list[str], BarView] | None:
        frames: list[pl.DataFrame] = []
        query = daily_range_query(
            self.config,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            daily_table=daily_table,
        )
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                if not frame.is_empty():
                    frames.append(frame)
        if not frames:
            return None
        return daily_family_frame_to_view(pl.concat(frames, how="vertical"), device=device)

    def read_daily_views(
        self,
        *,
        tickers: tuple[str, ...],
        start_date: str,
        end_date: str,
        daily_table: str,
        device: torch.device | str = "cpu",
    ) -> dict[str, tuple[list[str], BarView]]:
        frames: list[pl.DataFrame] = []
        query = daily_tickers_range_query(
            self.config,
            tickers=tickers,
            start_date=start_date,
            end_date=end_date,
            daily_table=daily_table,
        )
        with self.record_batches(query) as batches:
            for batch in batches:
                frame = pl.from_arrow(batch)
                if not frame.is_empty():
                    frames.append(frame)
        if not frames:
            return {}
        combined = pl.concat(frames, how="vertical")
        result: dict[str, tuple[list[str], BarView]] = {}
        for part in combined.partition_by("ticker", maintain_order=True):
            ticker = str(part["ticker"][0]).upper()
            result[ticker] = daily_family_frame_to_view(part, device=device)
        return result


def frame_to_dense_view(frame: pl.DataFrame, *, device: torch.device | str = "cpu") -> BarView:
    if frame.is_empty():
        raise ValueError("cannot convert an empty bar frame")
    if frame["ticker"].n_unique() != 1 or frame["local_date"].n_unique() != 1:
        raise ValueError("frame_to_dense_view requires one ticker/session")
    frame = frame.sort("bar_start_us")
    feature_array = np.array(frame.select(list(FEATURE_NAMES)).to_numpy(), dtype=np.float32, copy=True)
    sparse = BarView(
        features=torch.as_tensor(feature_array, device=device),
        bar_start_us=torch.as_tensor(np.array(frame["bar_start_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(np.array(frame["bar_end_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(np.array(frame["available_at_us"].to_numpy(), copy=True), dtype=torch.long, device=device),
    )
    local_date = dt.date.fromisoformat(str(frame["local_date"][0]))
    midnight = dt.datetime.combine(local_date, dt.time(), tzinfo=ZoneInfo(SESSION_TIMEZONE))
    clock_start_us = int((midnight + dt.timedelta(seconds=SESSION_START_SECOND)).timestamp() * 1_000_000)
    clock_end_us = int((midnight + dt.timedelta(seconds=SESSION_END_SECOND)).timestamp() * 1_000_000)
    return densify_one_second_view(sparse, clock_start_us=clock_start_us, clock_end_us=clock_end_us)


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
    unknown_families = sorted(set(str(value) for value in frame["bar_family"].to_list()) - set(family_prefix))
    if unknown_families:
        raise ValueError(f"unsupported daily bar families: {unknown_families}")
    event_counts = {family: np.zeros(starts.shape[0], dtype=np.float64) for family in family_prefix}
    for row_index, family_value in enumerate(frame["bar_family"].to_list()):
        family = str(family_value)
        prefix = family_prefix.get(family)
        if prefix is None:
            raise AssertionError(f"validated daily family unexpectedly missing: {family}")
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
        bar_start_us=torch.as_tensor(np.array(starts, copy=True), dtype=torch.long, device=device),
        bar_end_us=torch.as_tensor(np.array(ends, copy=True), dtype=torch.long, device=device),
        available_at_us=torch.as_tensor(np.array(ends, copy=True), dtype=torch.long, device=device),
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


def held_out_tickers(tickers: tuple[str, ...], fraction: float, seed: int) -> tuple[str, ...]:
    ranked = sorted(
        (str(ticker).upper() for ticker in tickers),
        key=lambda ticker: hashlib.sha256(f"{seed}:{ticker}".encode("utf-8")).digest(),
    )
    count = max(1, min(len(ranked) - 1, round(len(ranked) * float(fraction))))
    return tuple(sorted(ranked[:count]))


def _view_prefix(view: BarView, *, last_available_us: int, max_rows: int) -> BarView | None:
    count = int(torch.searchsorted(view.available_at_us, torch.tensor(last_available_us, dtype=view.available_at_us.dtype), right=True))
    if count <= 0:
        return None
    start = max(0, count - max(1, int(max_rows)))
    return BarView(
        features=view.features[start:count],
        bar_start_us=view.bar_start_us[start:count],
        bar_end_us=view.bar_end_us[start:count],
        available_at_us=view.available_at_us[start:count],
    )


def _dummy_raw() -> torch.Tensor:
    return torch.zeros((1, len(FEATURE_NAMES)), dtype=torch.float32)


def build_session_examples(
    *,
    ticker: str,
    local_date: str,
    session: BarView,
    calendar_views: Mapping[str, BarView],
    config: DataConfig,
) -> Iterator[BarGPTExample]:
    """Yield non-overlapping origins while sharing one exact session rollup and target support."""
    context = int(config.context_bars_1s)
    right = int(config.right_support_bars_1s)
    maximum_origin = session.features.shape[0] - right
    if maximum_origin - context < int(config.min_origins_per_block):
        return
    full_views: dict[str, BarView] = {"1s": session}
    scale_names = {
        5_000_000: "5s",
        30_000_000: "30s",
        60_000_000: "1m",
        300_000_000: "5m",
        900_000_000: "15m",
        3_600_000_000: "1h",
    }
    for timeframe_us in config.intraday_timeframes_us:
        if int(timeframe_us) > int(config.base_timeframe_us):
            full_views[scale_names[int(timeframe_us)]] = rollup_intraday_view(session, int(timeframe_us))
    for origin_start in range(context, maximum_origin, int(config.origin_bars_1s)):
        origin_count = min(int(config.origin_bars_1s), maximum_origin - origin_start)
        if origin_count < int(config.min_origins_per_block):
            continue
        input_start = origin_start - context
        input_end = origin_start + origin_count
        support_end = input_end + right
        support = session.features[input_start:support_end]
        base_raw = session.features[input_start:input_end]
        origins = torch.arange(context, context + origin_count, dtype=torch.long)
        anchors = session.available_at_us[input_start:input_end][origins]
        last_anchor = int(anchors[-1])
        raw_views: dict[str, torch.Tensor] = {"1s": base_raw}
        asof: dict[str, torch.Tensor] = {}
        for name in ("5s", "30s", "1m", "5m", "15m", "1h"):
            view = full_views[name]
            duration = TIMEFRAME_US_BY_NAME[name]
            rows = max(8, context * int(config.base_timeframe_us) // duration + origin_count * int(config.base_timeframe_us) // duration + 4)
            prefix = _view_prefix(view, last_available_us=last_anchor, max_rows=rows)
            if prefix is None:
                raw_views[name] = _dummy_raw()
                asof[name] = torch.full((origin_count,), -1, dtype=torch.long)
            else:
                raw_views[name] = prefix.features
                asof[name] = causal_asof_indices(prefix.available_at_us, anchors)
        for name in ("1D", "1W", "1MO"):
            view = calendar_views.get(name)
            max_rows = int(config.daily_context_bars) if name == "1D" else max(24, int(config.daily_context_bars) // (5 if name == "1W" else 21))
            prefix = _view_prefix(view, last_available_us=last_anchor, max_rows=max_rows) if view is not None else None
            if prefix is None:
                raw_views[name] = _dummy_raw()
                asof[name] = torch.full((origin_count,), -1, dtype=torch.long)
            else:
                raw_views[name] = prefix.features
                asof[name] = causal_asof_indices(prefix.available_at_us, anchors)
        activity = float(base_raw[origins, FEATURE_INDEX["source_event_count"]].float().mean())
        regime = 0 if activity < config.activity_regime_low else (2 if activity >= config.activity_regime_high else 1)
        yield BarGPTExample(
            ticker=ticker,
            local_date=local_date,
            raw_views=raw_views,
            origin_indices=origins,
            asof_indices=asof,
            target_support=support,
            support_origin_indices=origins,
            horizons_us=config.horizons_us,
            base_timeframe_us=config.base_timeframe_us,
            activity_regime=regime,
        )


def _calendar_views(daily: tuple[list[str], BarView] | None) -> dict[str, BarView]:
    if daily is None:
        return {}
    dates, daily_view = daily
    result = {"1D": daily_view}
    for name in ("1W", "1MO"):
        rolled = rollup_calendar_view(daily_view, calendar_period_ids(dates, name))
        # A final calendar group has no following period proving that it closed; omit it
        # rather than leaking an incomplete lifecycle week/month as a completed bar.
        if rolled.features.shape[0] > 0:
            rolled = BarView(
                rolled.features[:-1], rolled.bar_start_us[:-1], rolled.bar_end_us[:-1], rolled.available_at_us[:-1]
            )
        result[name] = rolled
    return result


def balanced_regime_stream(
    source: Iterator[BarGPTExample],
    *,
    buffer_size: int,
    seed: int,
) -> Iterator[BarGPTExample]:
    """Deterministically resample bounded buffers so available activity regimes contribute equally."""
    rng = random.Random(seed)
    while True:
        buffer: list[BarGPTExample] = []
        try:
            for _ in range(max(3, int(buffer_size))):
                buffer.append(next(source))
        except StopIteration:
            pass
        if not buffer:
            return
        by_regime = {regime: [item for item in buffer if item.activity_regime == regime] for regime in range(3)}
        active = [regime for regime, items in by_regime.items() if items]
        for items in by_regime.values():
            rng.shuffle(items)
        schedule = [active[index % len(active)] for index in range(len(buffer))]
        rng.shuffle(schedule)
        offsets = {regime: 0 for regime in active}
        for regime in schedule:
            items = by_regime[regime]
            yield items[offsets[regime] % len(items)]
            offsets[regime] += 1
        if len(buffer) < max(3, int(buffer_size)):
            return


class BarGPTIterableDataset(IterableDataset[BarGPTExample]):
    """Worker-sharded, bounded ArrowStream loader over ticker/session units."""

    def __init__(
        self,
        *,
        data_config: DataConfig,
        stream_config: ClickHouseBarStreamConfig,
        split: str,
        seed: int,
        epoch: int = 0,
    ) -> None:
        super().__init__()
        if split not in {"train", "validation"}:
            raise ValueError("split must be train or validation")
        data_config.validate()
        self.data_config = data_config
        self.stream_config = stream_config
        self.split = split
        self.seed = int(seed)
        self.epoch = int(epoch)

    def _tickers(self) -> list[str]:
        held_out = set(held_out_tickers(self.data_config.tickers, self.data_config.validation_ticker_fraction, self.seed))
        selected = [ticker for ticker in self.data_config.tickers if (ticker in held_out) == (self.split == "validation")]
        selected.sort(key=lambda ticker: hashlib.sha256(f"{self.seed + self.epoch}:{ticker}".encode()).digest())
        worker = get_worker_info()
        if worker is not None:
            selected = selected[worker.id :: worker.num_workers]
        return selected

    def __iter__(self) -> Iterator[BarGPTExample]:
        client = ArrowStreamClient(self.stream_config)
        start_date = self.data_config.validation_start_date if self.split == "validation" else self.data_config.start_date
        end_date = self.data_config.end_date
        lookback_days = max(730, int(self.data_config.daily_context_bars * 2.2))
        daily_start = (dt.date.fromisoformat(start_date) - dt.timedelta(days=lookback_days)).isoformat()
        def raw_examples() -> Iterator[BarGPTExample]:
            tickers = tuple(self._tickers())
            daily_by_ticker = client.read_daily_views(
                tickers=tickers,
                start_date=daily_start,
                end_date=end_date,
                daily_table=self.data_config.daily_table,
            ) if tickers else {}
            for ticker in tickers:
                calendars = _calendar_views(daily_by_ticker.get(ticker))
                for local_date, session in client.iter_session_views(ticker=ticker, start_date=start_date, end_date=end_date):
                    yield from build_session_examples(
                        ticker=ticker,
                        local_date=local_date,
                        session=session,
                        calendar_views=calendars,
                        config=self.data_config,
                    )
        source = raw_examples()
        if self.data_config.balance_activity_regimes and self.split == "train":
            worker = get_worker_info()
            worker_id = worker.id if worker is not None else 0
            yield from balanced_regime_stream(
                source,
                buffer_size=max(3, self.data_config.ready_queue_blocks * self.data_config.batch_size * 3),
                seed=self.seed + self.epoch * 10_000 + worker_id,
            )
        else:
            yield from source


def make_dataloader(
    dataset: BarGPTIterableDataset,
    config: DataConfig,
    *,
    drop_last: bool,
) -> DataLoader[BarGPTExample]:
    kwargs: dict[str, object] = {}
    if config.loader_workers > 0:
        kwargs["prefetch_factor"] = max(1, math.ceil(int(config.ready_queue_blocks) / int(config.loader_workers)))
        kwargs["persistent_workers"] = bool(config.persistent_workers)
    return DataLoader(
        dataset,
        batch_size=int(config.batch_size),
        num_workers=int(config.loader_workers),
        pin_memory=bool(config.pin_memory),
        drop_last=drop_last,
        collate_fn=partial(collate_examples, balance_activity_regimes=config.balance_activity_regimes),
        **kwargs,
    )


def timeframe_contract() -> tuple[dict[str, int], dict[str, int]]:
    return dict(TIMEFRAME_US_BY_NAME), dict(PATHWAY_ID_BY_NAME)
