from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import torch
import websockets

from pipelines.market_sip.events.clickhouse_build_intraday_base_bars import (
    CONDITION_DIRECT_SOURCE_FAMILIES,
    CONDITION_INDICATOR_SOURCE_FAMILIES,
    FUTURE_CONDITION_GROUPS,
)
from research.bar_gpt.v3.corporate_actions import normalize_features_to_anchor
from research.bar_gpt.v3.data import BarView, rollup_calendar_view, rollup_intraday_view
from research.bar_gpt.v3.direct_event_shards import DirectEventArrowStreamClient
from research.bar_gpt.v3.loader import ArrowStreamClient, ClickHouseBarStreamConfig
from research.bar_gpt.v3.schema import FEATURE_INDEX, FEATURE_NAMES, SESSION_TIMEZONE
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)

from .cache import INTRADAY_VIEW_US, RawBar
from .models import LoadedRelease


NEW_YORK = ZoneInfo(SESSION_TIMEZONE)


@dataclass(frozen=True, slots=True)
class ConditionReferences:
    update_last: frozenset[int]
    update_high_low: frozenset[int]
    update_volume: frozenset[int]
    all_trade: frozenset[int]
    trade_model_ineligible: frozenset[int]
    all_quote: frozenset[int]
    quote_origin_ineligible: frozenset[int]
    condition_tokens: dict[str, frozenset[int]]

    @classmethod
    def load(cls, database: str, table: str) -> "ConditionReferences":
        client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=30
        )
        try:
            sql = f"""
SELECT source_family, modifier_int, token_id, update_high_low, update_last, update_volume
FROM `{database}`.`{table}` FINAL
WHERE is_join_canonical = 1
ORDER BY token_id
FORMAT JSONEachRow
"""
            rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
        finally:
            client.close()
        trade = [row for row in rows if row["source_family"] == "trade_conditions"]
        quote = [row for row in rows if row["source_family"] == "quote_conditions"]
        if not trade or not quote:
            raise RuntimeError("condition reference authority is missing canonical trade or quote rows")
        group_tokens: dict[str, frozenset[int]] = {}
        for label, groups in FUTURE_CONDITION_GROUPS:
            selected = set()
            for source_family, modifiers in groups:
                for row in rows:
                    family = str(row["source_family"])
                    direct_match = family == source_family
                    indicator_match = (
                        source_family in CONDITION_INDICATOR_SOURCE_FAMILIES
                        and family not in CONDITION_DIRECT_SOURCE_FAMILIES
                    )
                    if (direct_match or indicator_match) and int(row["modifier_int"]) in modifiers:
                        selected.add(int(row["token_id"]))
            group_tokens[label] = frozenset(selected)
        return cls(
            update_last=frozenset(int(row["token_id"]) for row in trade if int(row["update_last"]) == 1),
            update_high_low=frozenset(int(row["token_id"]) for row in trade if int(row["update_high_low"]) == 1),
            update_volume=frozenset(int(row["token_id"]) for row in trade if int(row["update_volume"]) == 1),
            all_trade=frozenset(int(row["token_id"]) for row in trade),
            trade_model_ineligible=frozenset(int(row["token_id"]) for row in trade if int(row["modifier_int"]) == 12),
            all_quote=frozenset(int(row["token_id"]) for row in quote),
            quote_origin_ineligible=frozenset(
                int(row["token_id"]) for row in quote
                if int(row["modifier_int"]) in {-1, 12, 15, 19, 20, 80, 83, 84}
            ),
            condition_tokens=group_tokens,
        )


class EventBarAccumulator:
    def __init__(self, ticker: str, start_us: int) -> None:
        self.ticker = ticker
        self.start_us = start_us
        self.values = [0.0] * len(FEATURE_NAMES)
        self.first_keys: dict[str, tuple[int, int]] = {}
        self.last_keys: dict[str, tuple[int, int]] = {}
        self.revision = 0
        self.emitted = False

    def apply(self, event: dict[str, Any], references: ConditionReferences, max_spread_bps: float) -> None:
        event_type = int(event.get("event_meta") or 0) & 1
        primary_scale = 10_000.0 if int(event.get("event_meta") or 0) & 2 else 100.0
        secondary_scale = 10_000.0 if int(event.get("event_meta") or 0) & 4 else 100.0
        primary_price = float(event.get("price_primary_int") or 0) / primary_scale
        secondary_price = float(event.get("price_secondary_int") or 0) / secondary_scale
        tokens = [int(event.get(f"condition_token_{index}") or 0) for index in range(1, 6)]
        nonzero = [value for value in tokens if value]
        key = (int(event["sip_timestamp_us"]), int(event.get("source_sequence") or 0))
        retained = False
        origin = False
        if event_type == 1:
            size = float(event.get("size_primary") or 0)
            structural = primary_price > 0 and size > 0
            known = bool(nonzero) and all(token in references.all_trade for token in nonzero)
            model_eligible = structural and known and not any(token in references.trade_model_ineligible for token in nonzero)
            update_last = model_eligible and all(token in references.update_last for token in nonzero)
            update_high_low = model_eligible and all(token in references.update_high_low for token in nonzero)
            update_volume = model_eligible and all(token in references.update_volume for token in nonzero)
            origin = update_last or update_high_low
            retained = origin or update_volume
            if origin:
                self._increment("trade_present", 1, maximum=True)
                self._ordered("trade_open", primary_price, key, first=True)
            if update_high_low:
                self._extreme("trade_high", primary_price, maximum=True)
                self._extreme("trade_low", primary_price, maximum=False)
            if update_last:
                self._ordered("trade_close", primary_price, key, first=False)
            if update_volume:
                self._add("trade_size_sum", size)
                self._ordered("trade_size_open", size, key, first=True)
                self._extreme("trade_size_high", size, maximum=True)
                self._extreme("trade_size_low", size, maximum=False)
                self._ordered("trade_size_close", size, key, first=False)
                self._add("trade_size_squared_sum", size * size)
                self._add("trade_event_count", 1)
            if update_volume and origin:
                self._add("trade_price_size_sum", primary_price * size)
                self._add("trade_price_eligible_size_sum", size)
            self._add("eligible_trade_event_count" if origin else "rejected_trade_event_count", 1)
            if structural and (not nonzero or not known):
                self._add("unknown_condition_event_count", 1)
        else:
            ask_size = float(event.get("size_primary") or 0)
            bid_size = float(event.get("size_secondary") or 0)
            quote_tokens = [value for value in tokens[:4] if value]
            midpoint = (primary_price + secondary_price) / 2.0
            spread = primary_price - secondary_price
            structural = (
                primary_price > 0 and secondary_price > 0 and ask_size > 0 and bid_size > 0
                and secondary_price <= primary_price
            )
            spread_bps = spread / midpoint * 10_000.0 if structural and midpoint > 0 else math.inf
            known = bool(quote_tokens) and all(token in references.all_quote for token in quote_tokens)
            origin = structural and known and spread_bps <= max_spread_bps and not any(
                token in references.quote_origin_ineligible for token in quote_tokens
            )
            retained = origin
            if origin:
                for family, price, size in (("bid", secondary_price, bid_size), ("ask", primary_price, ask_size)):
                    self._increment(f"{family}_present", 1, maximum=True)
                    self._ordered(f"{family}_open", price, key, first=True)
                    self._extreme(f"{family}_high", price, maximum=True)
                    self._extreme(f"{family}_low", price, maximum=False)
                    self._ordered(f"{family}_close", price, key, first=False)
                    self._add(f"{family}_size_sum", size)
                    self._ordered(f"{family}_size_open", size, key, first=True)
                    self._extreme(f"{family}_size_high", size, maximum=True)
                    self._extreme(f"{family}_size_low", size, maximum=False)
                    self._ordered(f"{family}_size_close", size, key, first=False)
                    self._add(f"{family}_size_squared_sum", size * size)
                    self._add(f"{family}_price_size_sum", price * size)
                    self._add(f"{family}_event_count", 1)
                microprice = (primary_price * bid_size + secondary_price * ask_size) / (ask_size + bid_size)
                imbalance = (bid_size - ask_size) / (bid_size + ask_size)
                self._increment("quote_pair_present", 1, maximum=True)
                self._add("quote_pair_count", 1)
                for name, value in (("spread", spread), ("midpoint", midpoint), ("microprice", microprice), ("queue_imbalance", imbalance)):
                    self._ordered(f"{name}_open", value, key, first=True)
                    self._extreme(f"{name}_high", value, maximum=True)
                    self._extreme(f"{name}_low", value, maximum=False)
                    self._ordered(f"{name}_close", value, key, first=False)
                    self._add(f"{name}_sum", value)
                    self._add(f"{name}_squared_sum", value * value)
                self._add("locked_quote_count", int(secondary_price == primary_price))
                self._add("crossed_quote_count", int(secondary_price > primary_price))
                self._add("eligible_quote_event_count", 1)
            else:
                self._add("rejected_quote_event_count", 1)
                if structural and (not quote_tokens or not known):
                    self._add("unknown_condition_event_count", 1)
        if origin:
            self._increment("context_eligible", 1, maximum=True)
            self._increment("origin_eligible", 1, maximum=True)
            self._add("origin_event_count", 1)
        if retained:
            self._add("condition_nonzero_count", len(nonzero))
            self._add("source_event_count", 1)
            any_condition = False
            for label, selected in references.condition_tokens.items():
                if any(token in selected for token in nonzero):
                    column = label.replace("_flag", "_count")
                    self._add(column, 1)
                    any_condition = True
            if any_condition:
                self._add("condition_event_count", 1)
        self.revision += 1

    def raw_bar(self, source_revision: str, available_at_us: int | None = None) -> RawBar | None:
        if self.values[FEATURE_INDEX["origin_event_count"]] <= 0:
            return None
        return RawBar(
            ticker=self.ticker,
            view="1s",
            bar_start_us=self.start_us,
            bar_end_us=self.start_us + 1_000_000,
            available_at_us=max(self.start_us + 1_000_000, int(available_at_us or 0)),
            values=tuple(self.values),
            revision=max(1, self.revision),
            source="qmd://compact-events/v4",
            source_revision=source_revision,
        )

    def _add(self, name: str, value: float) -> None:
        self.values[FEATURE_INDEX[name]] += float(value)

    def _increment(self, name: str, value: float, *, maximum: bool) -> None:
        index = FEATURE_INDEX[name]
        self.values[index] = max(self.values[index], float(value)) if maximum else self.values[index] + float(value)

    def _extreme(self, name: str, value: float, *, maximum: bool) -> None:
        index = FEATURE_INDEX[name]
        current = self.values[index]
        self.values[index] = value if current == 0 else (max(current, value) if maximum else min(current, value))

    def _ordered(self, name: str, value: float, key: tuple[int, int], *, first: bool) -> None:
        keys = self.first_keys if first else self.last_keys
        prior = keys.get(name)
        if prior is None or (key < prior if first else key >= prior):
            keys[name] = key
            self.values[FEATURE_INDEX[name]] = value


class LiveEventBarBuilder:
    def __init__(self, references: ConditionReferences, max_spread_bps: float, retain_seconds: int = 120) -> None:
        self.references = references
        self.max_spread_bps = float(max_spread_bps)
        self.retain_seconds = max(5, int(retain_seconds))
        self._bars: dict[str, OrderedDict[int, EventBarAccumulator]] = {}
        self.last_arrival_sequence = 0
        self.metrics = {"events": 0, "ignored": 0, "bars": 0, "corrections": 0}

    def apply(self, event: dict[str, Any], active_tickers: set[str]) -> list[RawBar]:
        ticker = str(event.get("ticker") or "").upper()
        if ticker not in active_tickers:
            self.metrics["ignored"] += 1
            return []
        timestamp = int(event.get("sip_timestamp_us") or 0)
        if timestamp <= 0:
            return []
        self.last_arrival_sequence = max(self.last_arrival_sequence, int(event.get("arrival_sequence") or 0))
        start = timestamp // 1_000_000 * 1_000_000
        ticker_bars = self._bars.setdefault(ticker, OrderedDict())
        accumulator = ticker_bars.setdefault(start, EventBarAccumulator(ticker, start))
        was_emitted = accumulator.emitted
        accumulator.apply(event, self.references, self.max_spread_bps)
        self.metrics["events"] += 1
        if was_emitted:
            bar = accumulator.raw_bar(str(self.last_arrival_sequence), timestamp)
            if bar is not None:
                self.metrics["corrections"] += 1
                return [bar]
        return []

    def flush(self, clock_us: int) -> list[RawBar]:
        emitted: list[RawBar] = []
        cutoff = clock_us - self.retain_seconds * 1_000_000
        for ticker, bars in list(self._bars.items()):
            for start, accumulator in list(bars.items()):
                if not accumulator.emitted and start + 1_000_000 <= clock_us:
                    accumulator.emitted = True
                    bar = accumulator.raw_bar(str(self.last_arrival_sequence))
                    if bar is not None:
                        emitted.append(bar)
                        self.metrics["bars"] += 1
                if start < cutoff:
                    del bars[start]
            if not bars:
                del self._bars[ticker]
        return emitted


class HistoricalBootstrap:
    def __init__(self, release: LoadedRelease) -> None:
        data = release.data_config
        stream_config = ClickHouseBarStreamConfig(
            url=default_clickhouse_url(),
            user=default_clickhouse_user(),
            password=default_clickhouse_password(),
            database=data.database,
            table=data.one_second_table,
            max_threads=max(1, int(data.clickhouse_max_threads_per_worker)),
            max_block_size=int(data.clickhouse_max_block_size),
            max_memory_usage=int(data.clickhouse_max_memory_usage),
            query_days=max(1, int(data.clickhouse_query_days)),
            retry_attempts=int(data.clickhouse_retry_attempts),
            retry_initial_seconds=float(data.clickhouse_retry_initial_seconds),
            retry_max_seconds=float(data.clickhouse_retry_max_seconds),
        )
        self.materialized = ArrowStreamClient(stream_config)
        self.direct = DirectEventArrowStreamClient(stream_config, data)
        self.release = release

    def load(self, ticker: str, as_of: dt.datetime) -> list[RawBar]:
        data = self.release.data_config
        local_day = as_of.astimezone(NEW_YORK).date()
        history_start = local_day - dt.timedelta(days=800)
        intraday_start = local_day - dt.timedelta(days=10)
        end = local_day + dt.timedelta(days=1)
        intervals = self.materialized.read_identity_intervals(
            (ticker,),
            identity_database=data.identity_database,
            interval_table=data.identity_interval_table,
            entity_table=data.identity_entity_table,
            event_table=data.identity_event_table,
            coverage_start=history_start.isoformat(),
        )[ticker]
        actions = self.materialized.read_split_actions(
            {ticker: intervals},
            start_date=history_start.isoformat(),
            end_date=end.isoformat(),
            split_database=data.split_database,
            split_table=data.split_table,
        )[ticker]
        session_views = list(self.direct.iter_session_views(
            ticker=ticker,
            start_date=intraday_start.isoformat(),
            end_date=end.isoformat(),
            source_intervals=intervals,
            prefetch_pages=1,
        ))
        raw: list[RawBar] = []
        anchor_us = int(as_of.timestamp() * 1_000_000)
        for _day, view in session_views:
            normalized = normalize_features_to_anchor(
                view.features, view.bar_start_us, anchor_us=anchor_us, actions=actions
            )
            normalized_view = BarView(normalized, view.bar_start_us, view.bar_end_us, view.available_at_us)
            raw.extend(_view_rows(ticker, "1s", normalized_view, normalized, "qmd-history:direct-events"))
            for name, timeframe_us in INTRADAY_VIEW_US.items():
                if name == "1s":
                    continue
                rolled = rollup_intraday_view(normalized_view, timeframe_us)
                raw.extend(_view_rows(ticker, name, rolled, rolled.features, "qmd-history:direct-events"))
        daily = self.materialized.read_daily_view(
            ticker=ticker,
            start_date=history_start.isoformat(),
            end_date=end.isoformat(),
            daily_table=data.daily_table,
            source_intervals=intervals,
        )
        if daily is not None:
            dates, view = daily
            normalized = normalize_features_to_anchor(
                view.features, view.bar_start_us, anchor_us=anchor_us, actions=actions
            )
            normalized_view = BarView(normalized, view.bar_start_us, view.bar_end_us, view.available_at_us)
            raw.extend(_view_rows(ticker, "1D", normalized_view, normalized, "qmd-history:daily-session-bars"))
            calendar_dates = [dt.date.fromisoformat(str(value)) for value in dates]
            identifiers = {
                "1W": torch.as_tensor([date.isocalendar().year * 100 + date.isocalendar().week for date in calendar_dates], dtype=torch.long),
                "1MO": torch.as_tensor([date.year * 100 + date.month for date in calendar_dates], dtype=torch.long),
            }
            for name, group_ids in identifiers.items():
                rolled = rollup_calendar_view(normalized_view, group_ids)
                raw.extend(_view_rows(ticker, name, rolled, rolled.features, "qmd-history:daily-session-bars"))
        return raw


async def consume_qmd_events(
    ws_base: str,
    active_tickers: Callable[[], set[str]],
    on_events: Callable[[list[dict[str, Any]]], Awaitable[None]],
    on_state: Callable[[str, str], None],
) -> None:
    delay = 0.5
    while True:
        try:
            on_state("connecting", "")
            subscribed = sorted(active_tickers())
            if not subscribed:
                on_state("idle", "no live tickers are scoped")
                await asyncio.sleep(0.25)
                continue
            ticker_query = urllib.parse.quote(",".join(subscribed), safe=",")
            uri = f"{ws_base}/stream/compact-events-batch?max_events=4096&max_delay_ms=25&tickers={ticker_query}"
            async with websockets.connect(uri, max_size=16 * 1024 * 1024, ping_interval=20) as socket:
                on_state("streaming", "")
                delay = 0.5
                async for message in socket:
                    if sorted(active_tickers()) != subscribed:
                        break
                    payload = json.loads(message)
                    if isinstance(payload, dict) and payload.get("type") == "stream_gap":
                        raise RuntimeError(f"QMD stream gap requires resnapshot: {payload}")
                    events = payload.get("events", []) if isinstance(payload, dict) else payload
                    if not isinstance(events, list):
                        events = [events]
                    selected = [row for row in events if str(row.get("ticker") or "").upper() in active_tickers()]
                    if selected:
                        await on_events(selected)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            on_state("degraded", str(exc))
            await asyncio.sleep(delay)
            delay = min(15.0, delay * 2.0)


def _view_rows(ticker: str, name: str, view: BarView, features: torch.Tensor, source: str) -> list[RawBar]:
    return [
        RawBar(
            ticker=ticker,
            view=name,
            bar_start_us=int(view.bar_start_us[index]),
            bar_end_us=int(view.bar_end_us[index]),
            available_at_us=int(view.available_at_us[index]),
            values=tuple(float(value) for value in features[index].tolist()),
            revision=1,
            source=source,
            source_revision="",
        )
        for index in range(features.shape[0])
    ]
