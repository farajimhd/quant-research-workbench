"""Squeeze-only population, full causal fields, certified structural intervals.

No Strategy 1 candidate or entry gate is used. The local copied loader owns
price-envelope/squeeze admission. Shared backend readers only certify and SELECT
producer products. SQL aggregates completed 100ms liquidity into one-second
execution/activity lanes before transfer; MACD is never computed here.
"""
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
import numpy as np
import polars as pl
import torch

from .encoding.catalog import arte_catalog
from .encoding.clickhouse import _clocks, certify_source, prepare_session
from .encoding.config import Funnel
from .source import arte_sql as sql
from .tape import SqueezeTape


def dependencies():
    wanted = {"close@1000ms", "high@1000ms", "low@1000ms", "volume@1000ms",
              "trade_count@1000ms"}
    wanted |= {f"{field}@{r}ms" for r in (1000, 5000, 10000, 30000)
               for field in ("macd_line", "macd_signal")}
    return tuple(f for f in arte_catalog().inputs if f.name in wanted)


def liquidity_sql(market, names, day, origin_us, lo, hi):
    """One row per actual completed second; no Python bucket loops."""
    units = {(u.ticker, u.stage): u for u in market.units}
    pairs = ",".join(f"({sql.literal(n)},toUUID({sql.literal(units[n, 'broker_100ms'].attempt_id)}))"
                     for n in names)
    completed_end = f"{origin_us}+(toInt64(bucket_index)+1)*100000"
    valid_quote = ("quote_valid=1 AND bid_int>0 AND ask_int>=bid_int AND quote_timestamp_us>0 "
                   f"AND quote_timestamp_us<=last_event_us AND last_event_us<{completed_end}")
    return f"""SELECT ticker,intDiv(toInt64(bucket_index),10)*1000000+1000000+{origin_us} AS time_us,
      sum(execution_volume) AS volume,sum(execution_notional) AS notional,
      argMax(cumulative_execution_volume,bucket_index) AS cumulative_volume,
      argMax(cumulative_execution_notional,bucket_index) AS cumulative_notional,
      argMaxIf(bid_int,bucket_index,{valid_quote})/10000. AS bid,
      argMaxIf(ask_int,bucket_index,{valid_quote})/10000. AS ask,
      argMaxIf(quote_timestamp_us,bucket_index,{valid_quote}) AS quote_us
      FROM arte.liquidity_100ms_v1 WHERE build_id={sql.literal(market.build_id)}
      AND session_date=toDate({sql.literal(day)}) AND (ticker,attempt_id) IN ({pairs})
      AND bucket_index>={(lo-origin_us)//100000} AND bucket_index<{(hi-origin_us)//100000}
      GROUP BY ticker,time_us ORDER BY ticker,time_us"""


def _align(frame, tickers, clocks, column, *, carry=False, tolerance=None):
    """Vectorized as-of alignment; NaN is an invalid update, never erased."""
    # Stable full grid is an input-boundary artifact, not per-candidate history.
    grid = pl.DataFrame({"time_us": clocks}).join(pl.DataFrame({"ticker": tickers}), how="cross")
    values = frame.select("ticker", "time_us", column).sort("time_us")
    if values.select("ticker", "time_us").is_duplicated().any():
        raise ValueError("Duplicate certified input keys")
    if carry:
        # A joined row without this source field is not an update. Explicit
        # NaN remains an update, preserving fail-closed invalid observations.
        values = values.filter(pl.col(column).is_not_null())
        result = grid.sort("time_us").join_asof(values, on="time_us", by="ticker",
                 strategy="backward", tolerance=tolerance, check_sortedness=False)
    else:
        result = grid.join(values, on=["ticker", "time_us"], how="left", validate="1:1")
    return result.sort("time_us", "ticker")[column].to_numpy().astype(np.float64).reshape(len(clocks), len(tickers))


def prepare_tape(session, settings, *, progress=print, maximum_gib=4.0):
    """Prepare once per session; never start trading or build missing products."""
    from src.backend.backtest_market_data import (certified_market_plan_from_arte,
        readonly_clickhouse_client, verify_market_day_plan)
    from src.backend.structural_v7_seed import certified_seed_plan
    from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan

    settings.validate()
    if session.strategy_ms != 1000 or session.broker_ms != 1000:
        raise ValueError("V2 uses a declared completed one-second research clock")
    # Enforce the repository runtime authority even for a custom cache argument.
    from .runtime import require_runtime
    require_runtime(session.runtime)
    receipt = certify_source(session)
    prepared = prepare_session(session, Funnel(), dependencies(),
        source_receipt=receipt, progress=lambda v: progress(json.dumps(v)))
    watch = prepared.watchlist.sort("ticker")
    tickers = tuple(watch["ticker"].to_list())
    if not tickers:
        raise ValueError("Certified empty squeeze population: no experiment tape")
    day, origin, start, end = _clocks(session)
    # Preserve the full current-session prefix for swing confirmation and entry
    # crossings, but gate trading at the requested start in the provenance.
    first = origin + 14_400_000_000 + 1_000_000
    clocks = np.arange(first, end + 1, 1_000_000, dtype=np.int64)
    estimate = len(clocks) * len(tickers) * (18 * 8 + 3)
    if estimate > maximum_gib * 1024**3:
        raise MemoryError("Declared union tape exceeds memory guard before allocation")
    configuration = {"market_day_build_id": receipt.source["build_id"],
                     "strategy": {"execution_interval": "100ms"},
                     "features": [{"timeframe": f"{r}s"} for r in (1, 5, 10, 30)]}
    market = certified_market_plan_from_arte(sessions=(str(day),), tickers=tickers,
                                           configuration=configuration)
    if market.build_id != receipt.source["build_id"]:
        raise ValueError("Signal and execution certificates bind different source builds")
    frames = []
    with closing(readonly_clickhouse_client(market_stream=True, v3_read_principal=True)) as reader:
        verify_market_day_plan(market, reader)
        seeds = certified_seed_plan(market, reader)
        structure = certify_v7_interval_plan(market, seeds, session_date=str(day),
                                             candidate_tickers=tickers, client=reader)
        for offset in range(0, len(tickers), session.fetch_tickers):
            names = tickers[offset:offset + session.fetch_tickers]
            for lo in range(first - 1_000_000, end, session.fetch_seconds * 1_000_000):
                hi = min(end, lo + session.fetch_seconds * 1_000_000)
                chunks = reader.iter_arrow_record_batches(liquidity_sql(market, names, day, origin, lo, hi))
                frames.extend(pl.from_arrow(batch) for batch in chunks)
            progress(f"Liquidity prepared {min(offset + len(names), len(tickers))}/{len(tickers)} tickers")
    if not frames:
        raise ValueError("Certified squeeze population has no liquidity evidence")
    liquid = pl.concat(frames).sort("ticker", "time_us")
    liquid = liquid.with_columns(
        pl.when(pl.col("volume") > 0).then(pl.col("notional") / pl.col("volume")).otherwise(None).alias("fill_price"),
        pl.when(pl.col("cumulative_volume") > 0).then(pl.col("cumulative_notional") / pl.col("cumulative_volume")).otherwise(None).alias("vwap"))
    identities = watch.select("ticker", "listing_id")
    bars = prepared.features[1000].join(identities, on="listing_id", validate="m:1")
    # A missing interval has zero executable capacity, distinct from its valid
    # source coverage. Invalid published values still fail validation.
    arrays = {name: _align(liquid, tickers, clocks, name, carry=name in ("vwap", "bid", "ask", "quote_us"))
              for name in ("volume", "notional", "fill_price", "vwap", "bid", "ask", "quote_us")}
    arrays["trades"] = _align(bars, tickers, clocks, "trade_count_1000")
    observed = _align(bars, tickers, clocks, "price_valid_1000") == 1
    close = _align(bars, tickers, clocks, "close_int_1000", carry=True) / 10000
    raw_valid = _align(bars, tickers, clocks, "price_valid_1000", carry=True) == 1
    close[~raw_valid] = np.nan
    extrema = _align(bars, tickers, clocks, "extremes_valid_1000") == 1
    high = _align(bars, tickers, clocks, "high_int_1000") / 10000
    low = _align(bars, tickers, clocks, "low_int_1000") / 10000
    high[~extrema], low[~extrema] = np.nan, np.nan
    macd = []
    for field in ("macd_line", "macd_signal"):
        lanes = []
        for resolution in (1000, 5000, 10000, 30000):
            frame = prepared.features[resolution].join(identities, on="listing_id", validate="m:1")
            if not frame.height:
                raise ValueError(f"Missing certified MACD resolution {resolution}")
            lanes.append(_align(frame, tickers, clocks, f"{field}_{resolution}", carry=True,
                                tolerance=resolution * 1000))
        macd.append(np.stack(lanes, axis=-1))
    intervals = dict(structure.intervals)
    l = max(15, max(len(intervals[n]) for n in tickers))
    if l > 4096:
        raise MemoryError("Structural interval envelope exceeds 4096; no truncation")
    level_from = np.zeros((len(tickers), l), dtype=np.int64)
    level_to = np.zeros_like(level_from)
    lower = np.full((len(tickers), l), np.nan)
    resistance = np.zeros((len(tickers), l), dtype=bool)
    valid_clock = np.zeros((len(clocks), len(tickers)), dtype=bool)
    seconds = (clocks - origin - 14_400_000_000) // 1000
    certified_seconds = dict(structure.valid_seconds)
    for ni, ticker in enumerate(tickers):
        valid_clock[:, ni] = np.isin(seconds, certified_seconds[ticker])
        for li, interval in enumerate(intervals[ticker]):
            # These are already producer-confirmed availability intervals.
            level_from[ni, li] = origin // 1_000_000 + 14400 + (interval.valid_from_ms + 999) // 1000
            level_to[ni, li] = origin // 1_000_000 + 14400 + (interval.valid_to_ms + 999) // 1000
            lower[ni, li] = interval.lower
            resistance[ni, li] = interval.role == "resistance"
    quote_age = clocks[:, None] - arrays.pop("quote_us")
    quote_valid = ((quote_age >= 0) & (quote_age <= settings.maximum_quote_age_seconds * 1_000_000)
                   & (arrays["bid"] > 0) & (arrays["ask"] >= arrays["bid"]))
    for name in ("volume", "notional", "trades"):
        arrays[name] = np.nan_to_num(arrays[name], nan=0.0, posinf=float("inf"), neginf=-float("inf"))
    tensor = lambda a: torch.from_numpy(np.ascontiguousarray(a))
    provenance = {"version": "squeeze-union-tape-v2", "synthetic": False,
                  "source_key": prepared.source_key, "market_token": market.token,
                  "structural_token": structure.token, "seed_token": seeds.token,
                  "source_build": market.build_id, "session": str(day),
                  "start_second": start // 1_000_000, "end_second": end // 1_000_000,
                  "funnel": "released-100ms-squeeze-price-envelope-1-to-50",
                  "structural_interval_ids": [[r.level_id for r in intervals[n]] for n in tickers],
                  "preparation": prepared.metrics}
    provenance["fingerprint"] = sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    return SqueezeTape(tickers, tensor(clocks // 1_000_000),
        tensor((watch["admitted_at_us"].to_numpy() + 999999) // 1_000_000),
        tensor(close), tensor(observed), tensor(high), tensor(low), tensor(arrays.pop("vwap")),
        tensor(arrays.pop("bid")), tensor(arrays.pop("ask")), tensor(quote_valid),
        tensor(arrays.pop("volume")), tensor(arrays.pop("notional")), tensor(arrays.pop("trades")),
        tensor(arrays.pop("fill_price")), tensor(macd[0]), tensor(macd[1]), tensor(valid_clock),
        tensor(level_from), tensor(level_to), tensor(lower), tensor(resistance), provenance).validate()
