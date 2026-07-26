from __future__ import annotations

import bisect
import datetime as dt
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient
from research.news_reaction_model.v16.market_context import (
    MARKET_WINDOW_NAMES,
    MARKET_WINDOWS_SECONDS,
    encode_current_market_features,
)


EXCHANGE_TZ = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
MINUTE_US = 60_000_000


def _q(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _qi(value: str) -> str:
    return "`" + str(value).replace("`", "``") + "`"


def event_table_for_date(base: str, session_date: dt.date) -> str:
    return f"{base}_{session_date.year}" if not base.rsplit("_", 1)[-1].isdigit() else base


def daily_minute_bars_sql(
    config: Any,
    session_date: dt.date,
    *,
    tickers: Sequence[str] | None = None,
) -> str:
    source_dates = (session_date, session_date + dt.timedelta(days=1))
    source_tables = tuple(
        dict.fromkeys(event_table_for_date(config.events_table_base, value) for value in source_dates)
    )
    qualified = [
        f"{_qi(config.market_database)}.{_qi(table)}" for table in source_tables
    ]
    source = (
        qualified[0]
        if len(qualified) == 1
        else "("
        + " UNION ALL ".join(
            f"SELECT * FROM {table} WHERE event_date >= toDate({_q(session_date.isoformat())}) "
            f"AND event_date <= toDate({_q(session_date.isoformat())}) + INTERVAL 1 DAY"
            for table in qualified
        )
        + ")"
    )
    condition_reference = (
        f"{_qi(config.market_database)}.{_qi(config.condition_reference_table)}"
    )
    ticker_values = tuple(
        dict.fromkeys(str(value).strip().upper() for value in (tickers or ()) if str(value).strip())
    )
    ticker_predicate = (
        f" AND ticker IN ({','.join(_q(value) for value in ticker_values)})"
        if ticker_values
        else ""
    )
    # event_date is a UTC partition key. One adjacent day is required because
    # the 04:00-20:00 New York session crosses UTC date boundaries in DST.
    return f"""
WITH
    (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference}
     WHERE source_family = 'trade_conditions' AND is_join_canonical = 1
       AND update_last = 1) AS update_last_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference}
     WHERE source_family = 'trade_conditions' AND is_join_canonical = 1
       AND update_high_low = 1) AS update_high_low_tokens,
    (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference}
     WHERE source_family = 'trade_conditions' AND is_join_canonical = 1
       AND update_last = 1 AND update_high_low = 1) AS fully_price_eligible_tokens,
    (SELECT any(toUInt8(token_id)) FROM {condition_reference}
     WHERE source_family = 'trade_conditions' AND is_join_canonical = 1
       AND modifier_int = 12) AS form_t_token,
source AS
(
    SELECT
        ticker,
        ordinal,
        sip_timestamp_us,
        bitAnd(event_meta, 1) = 1 AS is_trade,
        if(
            bitAnd(event_meta, 2) = 2,
            toFloat64(price_primary_int) / 10000.0,
            toFloat64(price_primary_int) / 100.0
        ) AS trade_price,
        toFloat64(size_primary) AS trade_size,
        if(
            bitAnd(event_meta, 4) = 4,
            toFloat64(price_secondary_int) / 10000.0,
            toFloat64(price_secondary_int) / 100.0
        ) AS bid_price,
        if(
            bitAnd(event_meta, 2) = 2,
            toFloat64(price_primary_int) / 10000.0,
            toFloat64(price_primary_int) / 100.0
        ) AS ask_price,
        fromUnixTimestamp64Micro(toInt64(sip_timestamp_us), 'UTC') AS event_utc,
        toTimeZone(event_utc, 'America/New_York') AS event_et,
        toDate(event_et) AS session_date_et,
        toHour(event_et) * 60 + toMinute(event_et) AS session_minute_et,
        arrayFilter(
            token -> token != 0,
            [condition_token_1, condition_token_2, condition_token_3,
             condition_token_4, condition_token_5]
        ) AS condition_tokens
    FROM {source}
    WHERE event_date >= toDate({_q(session_date.isoformat())})
      AND event_date <= toDate({_q(session_date.isoformat())}) + INTERVAL 1 DAY
      AND ticker != '' AND sip_timestamp_us > 0 AND ordinal > 0
      {ticker_predicate}
),
classified AS
(
    SELECT
        *,
        toUInt8(
            empty(condition_tokens)
            OR if(
                (session_minute_et < 570 OR session_minute_et >= 960)
                    AND has(condition_tokens, form_t_token)
                    AND arrayAll(
                        token -> token = form_t_token
                            OR has(fully_price_eligible_tokens, token),
                        condition_tokens
                    ),
                1,
                arrayAll(token -> has(update_last_tokens, token), condition_tokens)
            )
        ) AS update_last,
        toUInt8(
            empty(condition_tokens)
            OR if(
                (session_minute_et < 570 OR session_minute_et >= 960)
                    AND has(condition_tokens, form_t_token)
                    AND arrayAll(
                        token -> token = form_t_token
                            OR has(fully_price_eligible_tokens, token),
                        condition_tokens
                    ),
                1,
                arrayAll(token -> has(update_high_low_tokens, token), condition_tokens)
            )
        ) AS update_high_low,
        toUInt64(
            intDiv(toUInt64(sip_timestamp_us), 60000000) * 60000000 + 60000000
        ) AS minute_end_us
    FROM source
    WHERE session_date_et = toDate({_q(session_date.isoformat())})
      AND session_minute_et >= 240 AND session_minute_et < 1200
)
SELECT
    ticker,
    minute_end_us,
    argMinIf(
        trade_price, tuple(sip_timestamp_us, ordinal),
        is_trade AND trade_price > 0 AND trade_size > 0 AND update_last = 1
    ) AS open,
    maxIf(
        trade_price,
        is_trade AND trade_price > 0 AND trade_size > 0 AND update_high_low = 1
    ) AS high,
    minIf(
        trade_price,
        is_trade AND trade_price > 0 AND trade_size > 0 AND update_high_low = 1
    ) AS low,
    argMaxIf(
        trade_price, tuple(sip_timestamp_us, ordinal),
        is_trade AND trade_price > 0 AND trade_size > 0 AND update_last = 1
    ) AS close,
    sumIf(
        trade_size,
        is_trade AND trade_price > 0 AND trade_size > 0 AND update_last = 1
    ) AS volume,
    sumIf(
        trade_price * trade_size,
        is_trade AND trade_price > 0 AND trade_size > 0 AND update_last = 1
    ) AS dollar_volume,
    countIf(
        is_trade AND trade_price > 0 AND trade_size > 0 AND update_last = 1
    ) AS trade_count,
    countIf(NOT is_trade AND bid_price > 0 AND ask_price > 0 AND ask_price >= bid_price)
        AS quote_count
FROM classified
GROUP BY ticker, minute_end_us
HAVING trade_count > 0 AND open > 0 AND close > 0 AND high > 0 AND low > 0
ORDER BY minute_end_us, ticker
SETTINGS max_threads={int(config.market_max_threads)},
 max_memory_usage={_q(config.market_max_memory_usage)}
FORMAT TabSeparatedRaw
"""


def prior_daily_volume_sql(config: Any, session_date: dt.date) -> str:
    table = f"{_qi(config.market_database)}.{_qi(config.macro_bar_table)}"
    return f"""
SELECT sym AS ticker, avg(daily_volume) AS average_daily_volume
FROM
(
    SELECT sym, session_date, sum(size_sum) AS daily_volume
    FROM {table} FINAL
    WHERE timeframe = '1d' AND bar_family = 'trade'
      AND session_date < toDate({_q(session_date.isoformat())})
      AND session_date >= toDate({_q(session_date.isoformat())}) - 45
    GROUP BY sym, session_date
    ORDER BY sym, session_date DESC
    LIMIT 20 BY sym
)
GROUP BY sym
ORDER BY ticker
SETTINGS max_threads={int(config.market_max_threads)},
 max_memory_usage={_q(config.market_max_memory_usage)}
FORMAT TabSeparatedRaw
"""


MinuteBarRow = tuple[str, int, float, float, float, float, float, float, int, int]


def parse_minute_bar_rows(text: str) -> list[MinuteBarRow]:
    """Decode the bounded ClickHouse result without per-row JSON dictionaries."""
    rows: list[MinuteBarRow] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 10:
            raise RuntimeError(
                f"Malformed minute-bar row {line_number}: expected 10 fields, "
                f"received {len(fields)}."
            )
        rows.append(
            (
                fields[0],
                int(fields[1]),
                float(fields[2]),
                float(fields[3]),
                float(fields[4]),
                float(fields[5]),
                float(fields[6]),
                float(fields[7]),
                int(fields[8]),
                int(fields[9]),
            )
        )
    return rows


def parse_daily_volume_rows(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 2:
            raise RuntimeError(
                f"Malformed daily-volume row {line_number}: expected 2 fields, "
                f"received {len(fields)}."
            )
        value = float(fields[1])
        if value > 0:
            result[fields[0]] = value
    return result


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    end_us: int
    active_tickers: tuple[str, ...]
    session_return: np.ndarray
    volume: np.ndarray
    dollar_volume: np.ndarray
    relative_volume: np.ndarray
    leaders: tuple[str, ...]
    ticker_to_index: Mapping[str, int]
    sorted_return: np.ndarray
    sorted_volume: np.ndarray
    sorted_dollar_volume: np.ndarray
    sorted_relative_volume: np.ndarray
    top10_gainer: frozenset[int]
    top20_gainer: frozenset[int]
    top10_loser: frozenset[int]
    top20_loser: frozenset[int]
    top10_volume: frozenset[int]
    top20_volume: frozenset[int]
    top10_relative_volume: frozenset[int]
    top20_relative_volume: frozenset[int]

    def ranks_for(self, ticker: str) -> dict[str, Any]:
        index = self.ticker_to_index.get(ticker)
        if index is None:
            return {}

        def percentile(values: np.ndarray, value: float) -> float:
            if not values.size or not np.isfinite(value):
                return 0.0
            return float(np.searchsorted(values, value, side="right") / values.size)

        return {
            "return_percentile": percentile(
                self.sorted_return, self.session_return[index]
            ),
            "volume_percentile": percentile(self.sorted_volume, self.volume[index]),
            "dollar_volume_percentile": percentile(
                self.sorted_dollar_volume, self.dollar_volume[index]
            ),
            "relative_volume_percentile": percentile(
                self.sorted_relative_volume, self.relative_volume[index]
            ),
            "is_top10_gainer": index in self.top10_gainer,
            "is_top20_gainer": index in self.top20_gainer,
            "is_top10_loser": index in self.top10_loser,
            "is_top20_loser": index in self.top20_loser,
            "is_top10_volume": index in self.top10_volume,
            "is_top20_volume": index in self.top20_volume,
            "is_top10_relative_volume": index in self.top10_relative_volume,
            "is_top20_relative_volume": index in self.top20_relative_volume,
        }


class DayMarketData:
    """One bounded session of causal completed-minute market state."""

    def __init__(
        self,
        session_date: dt.date,
        rows: Sequence[Mapping[str, Any] | MinuteBarRow],
        average_daily_volume: Mapping[str, float] | None = None,
        *,
        rows_chronological: bool = False,
    ) -> None:
        self.session_date = session_date
        self.average_daily_volume = dict(average_daily_volume or {})
        clean = [
            (
                (
                    str(row["ticker"]),
                    int(row["minute_end_us"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                    float(row["dollar_volume"]),
                    int(row["trade_count"]),
                    int(row["quote_count"]),
                )
                if isinstance(row, Mapping)
                else row
            )
            for row in rows
        ]
        if not rows_chronological:
            clean.sort(key=lambda value: (value[1], value[0]))
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for row in clean:
            grouped.setdefault(row[0], []).append(row)
        self._by_ticker = {
            ticker: {
                "end": np.asarray([row[1] for row in values], dtype=np.int64),
                "open": np.asarray([row[2] for row in values], dtype=np.float64),
                "high": np.asarray([row[3] for row in values], dtype=np.float64),
                "low": np.asarray([row[4] for row in values], dtype=np.float64),
                "close": np.asarray([row[5] for row in values], dtype=np.float64),
                "volume": np.asarray([row[6] for row in values], dtype=np.float64),
                "dollar": np.asarray([row[7] for row in values], dtype=np.float64),
                "trades": np.asarray([row[8] for row in values], dtype=np.float64),
                "quotes": np.asarray([row[9] for row in values], dtype=np.float64),
            }
            for ticker, values in grouped.items()
        }
        for data in self._by_ticker.values():
            for name in ("volume", "dollar", "trades", "quotes"):
                data[f"{name}_prefix"] = np.concatenate(
                    (np.zeros(1, dtype=np.float64), np.cumsum(data[name]))
                )
        self._tickers = tuple(self._by_ticker)
        self._ticker_to_index = {
            ticker: index for index, ticker in enumerate(self._tickers)
        }
        self._snapshot_cache: dict[int, MarketSnapshot] = {}

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(self._by_ticker)

    def minute_rows(self, ticker: str) -> list[dict[str, Any]]:
        """Return a copy of the authoritative eligible-trade minute path."""
        data = self._by_ticker.get(str(ticker).strip().upper())
        if data is None:
            return []
        return [
            {
                "minute_end_us": int(data["end"][index]),
                "open": float(data["open"][index]),
                "high": float(data["high"][index]),
                "low": float(data["low"][index]),
                "close": float(data["close"][index]),
                "volume": float(data["volume"][index]),
                "dollar_volume": float(data["dollar"][index]),
                "trade_count": int(data["trades"][index]),
                "quote_count": int(data["quotes"][index]),
            }
            for index in range(len(data["end"]))
        ]

    def window(
        self,
        ticker: str,
        *,
        end_us: int,
        seconds: int | None,
        start_us: int | None = None,
    ) -> dict[str, Any]:
        data = self._by_ticker.get(ticker)
        if data is None:
            return {"available": False}
        ends = data["end"]
        right = int(np.searchsorted(ends, int(end_us), side="right"))
        if start_us is None:
            start_us = -1 if seconds is None else int(end_us) - int(seconds) * 1_000_000
        left = int(np.searchsorted(ends, int(start_us), side="right"))
        if right <= left:
            return {"available": False}
        opening = float(data["open"][left])
        close = float(data["close"][right - 1])
        if opening <= 0 or close <= 0:
            return {"available": False}
        volume = float(data["volume_prefix"][right] - data["volume_prefix"][left])
        dollar = float(data["dollar_prefix"][right] - data["dollar_prefix"][left])
        vwap = dollar / volume if volume > 0 else close
        return {
            "available": True,
            "terminal_return": close / opening - 1.0,
            "high_return": float(data["high"][left:right].max()) / opening - 1.0,
            "low_return": float(data["low"][left:right].min()) / opening - 1.0,
            "volume": volume,
            "dollar_volume": dollar,
            "trade_count": float(data["trades_prefix"][right] - data["trades_prefix"][left]),
            "quote_count": float(data["quotes_prefix"][right] - data["quotes_prefix"][left]),
            "vwap_distance": close / vwap - 1.0 if vwap > 0 else 0.0,
        }

    def post_news_window(
        self,
        ticker: str,
        *,
        published_us: int,
        observed_through_us: int,
        horizon_seconds: int | None,
    ) -> dict[str, Any]:
        horizon_end = (
            int(observed_through_us)
            if horizon_seconds is None
            else min(
                int(observed_through_us),
                int(published_us) + int(horizon_seconds) * 1_000_000,
            )
        )
        complete = horizon_seconds is None or (
            int(published_us) + int(horizon_seconds) * 1_000_000
            <= int(observed_through_us)
        )
        if not complete:
            return {"available": False}
        # The publication minute is intentionally excluded because a one-minute
        # aggregate cannot separate pre-publication and post-publication events.
        first_clean_end = ((int(published_us) + MINUTE_US - 1) // MINUTE_US + 1) * MINUTE_US
        return self.window(
            ticker,
            end_us=horizon_end,
            seconds=None,
            start_us=first_clean_end - 1,
        )

    def current_features(
        self,
        ticker: str,
        published_us: int,
        snapshot: MarketSnapshot | None = None,
    ) -> np.ndarray:
        completed_end = int(published_us // MINUTE_US * MINUTE_US)
        windows = {
            name: self.window(ticker, end_us=completed_end, seconds=seconds)
            for name, seconds in zip(MARKET_WINDOW_NAMES, MARKET_WINDOWS_SECONDS)
        }
        session = self.window(ticker, end_us=completed_end, seconds=None)
        snapshot = snapshot or self.snapshot(completed_end)
        return encode_current_market_features(
            windows,
            session,
            snapshot.ranks_for(ticker),
        )

    def snapshot(self, end_us: int) -> MarketSnapshot:
        end_us = int(end_us)
        cached = self._snapshot_cache.get(end_us)
        if cached is not None:
            return cached
        tickers: list[str] = []
        returns: list[float] = []
        volumes: list[float] = []
        dollars: list[float] = []
        relatives: list[float] = []
        for ticker in self._tickers:
            data = self._by_ticker[ticker]
            right = int(np.searchsorted(data["end"], end_us, side="right"))
            if right <= 0:
                tickers.append(ticker)
                returns.append(np.nan)
                volumes.append(np.nan)
                dollars.append(np.nan)
                relatives.append(np.nan)
                continue
            opening = float(data["open"][0])
            close = float(data["close"][right - 1])
            volume = float(data["volume_prefix"][right])
            dollar = float(data["dollar_prefix"][right])
            baseline = float(self.average_daily_volume.get(ticker, 0.0))
            tickers.append(ticker)
            returns.append(close / opening - 1.0 if opening > 0 else np.nan)
            volumes.append(volume)
            dollars.append(dollar)
            relatives.append(volume / baseline if baseline > 0 else np.nan)
        ticker_tuple = tuple(tickers)
        return_array = np.asarray(returns, dtype=np.float64)
        volume_array = np.asarray(volumes, dtype=np.float64)
        dollar_array = np.asarray(dollars, dtype=np.float64)
        relative_array = np.asarray(relatives, dtype=np.float64)

        def top_indices(values: np.ndarray, count: int, descending: bool) -> Iterable[int]:
            valid = np.flatnonzero(np.isfinite(values))
            if not valid.size:
                return ()
            order = np.argsort(values[valid], kind="stable")
            if descending:
                order = order[::-1]
            return valid[order[:count]].tolist()

        gain_order = tuple(top_indices(return_array, 20, True))
        loss_order = tuple(top_indices(return_array, 20, False))
        volume_order = tuple(top_indices(volume_array, 20, True))
        relative_order = tuple(top_indices(relative_array, 20, True))
        ordered: list[int] = []
        for indices in (
            gain_order[:10],
            loss_order[:10],
            volume_order[:10],
            tuple(top_indices(dollar_array, 10, True)),
            relative_order[:10],
        ):
            for index in indices:
                if index not in ordered:
                    ordered.append(index)
                if len(ordered) >= 20:
                    break
            if len(ordered) >= 20:
                break
        snapshot = MarketSnapshot(
            end_us=end_us,
            active_tickers=ticker_tuple,
            session_return=return_array,
            volume=volume_array,
            dollar_volume=dollar_array,
            relative_volume=relative_array,
            leaders=tuple(ticker_tuple[index] for index in ordered),
            ticker_to_index=self._ticker_to_index,
            sorted_return=np.sort(return_array[np.isfinite(return_array)]),
            sorted_volume=np.sort(volume_array[np.isfinite(volume_array)]),
            sorted_dollar_volume=np.sort(
                dollar_array[np.isfinite(dollar_array)]
            ),
            sorted_relative_volume=np.sort(
                relative_array[np.isfinite(relative_array)]
            ),
            top10_gainer=frozenset(gain_order[:10]),
            top20_gainer=frozenset(gain_order[:20]),
            top10_loser=frozenset(loss_order[:10]),
            top20_loser=frozenset(loss_order[:20]),
            top10_volume=frozenset(volume_order[:10]),
            top20_volume=frozenset(volume_order[:20]),
            top10_relative_volume=frozenset(relative_order[:10]),
            top20_relative_volume=frozenset(relative_order[:20]),
        )
        self._snapshot_cache[end_us] = snapshot
        return snapshot

    def drop_snapshots(self) -> None:
        self._snapshot_cache.clear()


def load_day_market_data(
    client: ClickHouseHttpClient,
    config: Any,
    session_date: dt.date,
) -> DayMarketData:
    rows = parse_minute_bar_rows(
        client.execute(daily_minute_bars_sql(config, session_date))
    )
    baselines = parse_daily_volume_rows(
        client.execute(prior_daily_volume_sql(config, session_date))
    )
    return DayMarketData(
        session_date,
        rows,
        baselines,
        rows_chronological=True,
    )


class DayMarketCache:
    def __init__(
        self,
        client: ClickHouseHttpClient,
        config: Any,
        *,
        max_sessions: int = 5,
        prefetch_workers: int = 1,
    ) -> None:
        self.client = client
        self.config = config
        self.max_sessions = max(2, int(max_sessions))
        self.prefetch_workers = max(1, int(prefetch_workers))
        self._days: dict[dt.date, DayMarketData] = {}
        self._order: list[dt.date] = []
        self._scheduled: deque[dt.date] = deque()
        self._scheduled_set: set[dt.date] = set()
        self._pending: dict[
            dt.date, Future[tuple[DayMarketData, float]]
        ] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=self.prefetch_workers,
            thread_name_prefix="v16-market-day",
        )
        self._closed = False

    def _load_timed(self, session_date: dt.date) -> tuple[DayMarketData, float]:
        started = time.perf_counter()
        item = load_day_market_data(self.client, self.config, session_date)
        return item, time.perf_counter() - started

    def _fill_pending(self) -> None:
        if self._closed:
            return
        while self._scheduled and len(self._pending) < self.prefetch_workers:
            session_date = self._scheduled.popleft()
            self._scheduled_set.discard(session_date)
            if session_date in self._days or session_date in self._pending:
                continue
            self._pending[session_date] = self._executor.submit(
                self._load_timed, session_date
            )

    def prefetch(self, session_dates: Iterable[dt.date]) -> None:
        """Queue a bounded ordered lookahead without retaining an entire month."""
        for session_date in sorted(set(session_dates)):
            if (
                session_date in self._days
                or session_date in self._pending
                or session_date in self._scheduled_set
            ):
                continue
            self._scheduled.append(session_date)
            self._scheduled_set.add(session_date)
        self._fill_pending()

    def _install(self, session_date: dt.date, item: DayMarketData) -> None:
        self._days[session_date] = item
        bisect.insort(self._order, session_date)
        newest = self._order[-1]
        for day, cached in self._days.items():
            if day < newest:
                cached.drop_snapshots()
        while len(self._order) > self.max_sessions:
            expired = self._order.pop(0)
            self._days.pop(expired, None)

    def get(self, session_date: dt.date) -> DayMarketData:
        item = self._days.get(session_date)
        if item is not None:
            return item
        future = self._pending.pop(session_date, None)
        try:
            if future is None:
                item, elapsed = self._load_timed(session_date)
            else:
                item, elapsed = future.result()
        except Exception as exc:
            raise RuntimeError(
                f"V16 market session load failed for {session_date}."
            ) from exc
        self._install(session_date, item)
        self._fill_pending()
        print(
            f"MARKET READY | session={session_date} tickers={len(item.tickers):,} "
            f"load={elapsed:.1f}s pending={len(self._pending)} "
            f"queued={len(self._scheduled)}",
            flush=True,
        )
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._scheduled.clear()
        self._scheduled_set.clear()
        for future in self._pending.values():
            future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._pending.clear()

    def __enter__(self) -> "DayMarketCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
