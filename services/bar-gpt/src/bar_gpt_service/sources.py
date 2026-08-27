from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import math
import os
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import torch
import websockets

from pipelines.market_sip.events.clickhouse_build_intraday_base_bars import (
    CONDITION_DIRECT_SOURCE_FAMILIES,
    CONDITION_INDICATOR_SOURCE_FAMILIES,
    FUTURE_CONDITION_GROUPS,
)
from research.bar_gpt.v3.corporate_actions import normalize_features_to_anchor, split_execution_dates
from research.bar_gpt.v3.data import BarView, rollup_calendar_view, rollup_intraday_view
from research.bar_gpt.v3.direct_event_shards import DirectEventArrowStreamClient, append_daily
from research.bar_gpt.v3.loader import ArrowStreamClient, ClickHouseBarStreamConfig
from research.bar_gpt.v3.schema import FEATURE_INDEX, FEATURE_NAMES, SESSION_TIMEZONE
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)

from .cache import CALENDAR_VIEWS, INTRADAY_VIEW_US, RawBar
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
FROM `{database}`.`{table}`
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

    def source_revision(self, ticker: str, as_of: dt.datetime) -> dict[str, Any]:
        """Return the ticker/date-bounded QMD source plan and durable revision.

        The event authority is the exact per-ticker day index plus the source-file day
        manifest over the requested causal window. Identity intervals and split actions
        are resolved through the same readers used by the warm itself. This keeps the
        before/after integrity check fail-closed for relevant repairs without allowing an
        unrelated later-session insert in the same yearly table to starve historical
        Replay forever.
        """
        data = self.release.data_config
        local_day, history_start, end = _historical_window(as_of)
        canonical = ticker.upper()
        intervals = self.materialized.read_identity_intervals(
            (canonical,),
            identity_database=data.identity_database,
            interval_table=data.identity_interval_table,
            entity_table=data.identity_entity_table,
            event_table=data.identity_event_table,
            coverage_start=history_start.isoformat(),
        )[canonical]
        actions = self.materialized.read_split_actions(
            {canonical: intervals},
            start_date=history_start.isoformat(),
            end_date=end.isoformat(),
            split_database=data.split_database,
            split_table=data.split_table,
        )[canonical]
        client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=30
        )
        try:
            ticker_days_sql, source_days_sql = _bounded_event_revision_sql(
                data.database, canonical, history_start, end
            )
            ticker_days = [
                json.loads(line) for line in client.execute(ticker_days_sql).splitlines() if line.strip()
            ]
            source_days = [
                json.loads(line) for line in client.execute(source_days_sql).splitlines() if line.strip()
            ]
            condition_sql = f"""
SELECT database, table, sum(rows) AS row_count, max(max_block_number) AS max_block_number,
       max(data_version) AS max_data_version
FROM system.parts
WHERE active = 1
  AND database = '{_sql_text(data.database)}'
  AND table = '{_sql_text(data.condition_reference_table)}'
GROUP BY database, table
ORDER BY database, table
FORMAT JSONEachRow
"""
            condition_parts = [
                json.loads(line) for line in client.execute(condition_sql).splitlines() if line.strip()
            ]
        finally:
            client.close()
        if not ticker_days or not source_days or not condition_parts:
            raise RuntimeError(
                "QMD history source revision is incomplete; bounded ticker/source manifests "
                "or condition-reference parts are missing"
            )
        plan: dict[str, Any] = {
            "schema_version": 2,
            "authority": "qmd-history-bounded-manifests-v1",
            "ticker": canonical,
            "as_of_session": local_day.isoformat(),
            "coverage_start": history_start.isoformat(),
            "coverage_end_exclusive": end.isoformat(),
            "condition_reference_parts": condition_parts,
            "event_ticker_days": ticker_days,
            "source_days": source_days,
            "identity_intervals": [asdict(row) for row in intervals],
            "split_actions": [asdict(row) for row in actions],
        }
        encoded = json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        plan["revision_sha256"] = hashlib.sha256(encoded).hexdigest()
        return plan

    def load(
        self,
        ticker: str,
        as_of: dt.datetime,
        *,
        include_calendar: bool = True,
        stop_requested: Callable[[], bool] | None = None,
    ) -> list[RawBar]:
        def check_stopping() -> None:
            if stop_requested is not None and stop_requested():
                raise InterruptedError("BarGPT historical warm-up was cancelled for service shutdown")

        check_stopping()
        data = self.release.data_config
        local_day, history_start, end = _historical_window(as_of)
        intervals = self.materialized.read_identity_intervals(
            (ticker,),
            identity_database=data.identity_database,
            interval_table=data.identity_interval_table,
            entity_table=data.identity_entity_table,
            event_table=data.identity_event_table,
            coverage_start=history_start.isoformat(),
        )[ticker]
        check_stopping()
        actions = self.materialized.read_split_actions(
            {ticker: intervals},
            start_date=history_start.isoformat(),
            end_date=end.isoformat(),
            split_database=data.split_database,
            split_table=data.split_table,
        )[ticker]
        check_stopping()
        anchor_us = int(as_of.timestamp() * 1_000_000)
        retained_limits = {
            **data.intraday_context_by_name,
            **data.calendar_context_by_name,
            "1s": int(data.intraday_warmup_bars_1s),
            "1D": int(data.calendar_warmup_daily_bars),
        }
        raw_by_view: dict[str, list[RawBar]] = {
            view: [] for view in (*INTRADAY_VIEW_US, *CALENDAR_VIEWS)
        }

        def retain(rows: list[RawBar]) -> None:
            if not rows:
                return
            view = rows[0].view
            combined = [*raw_by_view[view], *rows]
            past = [row for row in combined if row.available_at_us <= anchor_us]
            future = [row for row in combined if row.available_at_us > anchor_us]
            raw_by_view[view] = [*past[-int(retained_limits[view]):], *future]

        session_views: list[tuple[str, BarView]] = []
        for lookback_days in (2, 4, 7, 10):
            check_stopping()
            session_views = list(self.direct.iter_session_views(
                ticker=ticker,
                start_date=(local_day - dt.timedelta(days=lookback_days)).isoformat(),
                end_date=end.isoformat(),
                source_intervals=intervals,
                # Serving already warms multiple tickers concurrently. Keep
                # each ticker to one raw query so model bootstrap cannot
                # monopolize ClickHouse ahead of interactive chart traffic.
                prefetch_pages=1,
            ))
            if _intraday_context_satisfied(session_views, data.intraday_context_by_name):
                break
        for _day, view in session_views:
            check_stopping()
            normalized = normalize_features_to_anchor(
                view.features, view.bar_start_us, anchor_us=anchor_us, actions=actions
            )
            normalized_view = BarView(normalized, view.bar_start_us, view.bar_end_us, view.available_at_us)
            retain(_view_rows(
                ticker, "1s", normalized_view, normalized, "qmd-history:direct-events",
                past_limit=int(retained_limits["1s"]), origin_us=anchor_us,
            ))
            for name, timeframe_us in INTRADAY_VIEW_US.items():
                if name == "1s":
                    continue
                rolled = rollup_intraday_view(normalized_view, timeframe_us)
                retain(_view_rows(
                    ticker, name, rolled, rolled.features, "qmd-history:direct-events",
                    past_limit=int(retained_limits[name]), origin_us=anchor_us,
                ))
        daily: tuple[list[str], BarView] | None = None
        if include_calendar:
            excluded_daily = split_execution_dates(actions)
            for day, view, _eligible_seconds in self.direct.iter_daily_views(
                ticker=ticker,
                start_date=history_start.isoformat(),
                end_date=end.isoformat(),
                source_intervals=intervals,
                prefetch_pages=1,
            ):
                check_stopping()
                if day in excluded_daily:
                    continue
                normalized = normalize_features_to_anchor(
                    view.features, view.bar_start_us, anchor_us=anchor_us, actions=actions
                )
                daily = append_daily(
                    daily,
                    ([day], BarView(normalized, view.bar_start_us, view.bar_end_us, view.available_at_us)),
                    max_rows=int(data.calendar_warmup_daily_bars) + 32,
                )
        check_stopping()
        if daily is not None:
            dates, normalized_view = daily
            retain(_view_rows(
                ticker,
                "1D",
                normalized_view,
                normalized_view.features,
                "qmd-history:direct-events-daily",
            ))
            calendar_dates = [dt.date.fromisoformat(str(value)) for value in dates]
            identifiers = {
                "1W": torch.as_tensor([date.isocalendar().year * 100 + date.isocalendar().week for date in calendar_dates], dtype=torch.long),
                "1MO": torch.as_tensor([date.year * 100 + date.month for date in calendar_dates], dtype=torch.long),
            }
            for name, group_ids in identifiers.items():
                rolled = rollup_calendar_view(normalized_view, group_ids)
                retain(_view_rows(
                    ticker,
                    name,
                    rolled,
                    rolled.features,
                    "qmd-history:direct-events-daily",
                ))
        return [row for view in (*INTRADAY_VIEW_US, *CALENDAR_VIEWS) for row in raw_by_view[view]]

    def load_current_session(
        self, ticker: str, as_of: dt.datetime
    ) -> list[RawBar]:
        local = as_of.astimezone(NEW_YORK)
        session_start = dt.datetime.combine(
            local.date(), dt.time(hour=4), tzinfo=NEW_YORK
        ).astimezone(dt.UTC)
        if as_of <= session_start:
            return []
        base_url = os.environ.get(
            "QMD_HISTORY_GATEWAY_URL", "http://127.0.0.1:8801"
        ).rstrip("/")
        params = {
            "start": session_start.isoformat().replace("+00:00", "Z"),
            "end": as_of.isoformat().replace("+00:00", "Z"),
            "tickers": ticker.upper(),
        }
        revision_url = f"{base_url}/source-revision?{urllib.parse.urlencode(params)}"
        revision_before = _read_json(revision_url)
        compact_params = {
            "start": params["start"],
            "end": params["end"],
            "limit": 100_000,
            "tail": "true",
        }
        compact_url = (
            f"{base_url}/snapshot/compact-events/{urllib.parse.quote(ticker.upper())}?"
            f"{urllib.parse.urlencode(compact_params)}"
        )
        events = _read_json(compact_url)
        if not isinstance(events, list):
            raise RuntimeError("QMD History compact-event response must be an array")
        if len(events) >= 100_000:
            raise RuntimeError(
                "QMD History current-session compact events exceed the bounded BarGPT warm limit"
            )
        revision_after = _read_json(revision_url)
        if revision_before != revision_after:
            raise RuntimeError("QMD History source revision changed during BarGPT session warm-up")
        if not bool(revision_after.get("request_complete")):
            raise RuntimeError("QMD History current-session source plan is incomplete")
        references = ConditionReferences.load(
            self.release.data_config.database,
            self.release.data_config.condition_reference_table,
        )
        builder = LiveEventBarBuilder(
            references,
            self.release.data_config.max_quote_spread_bps,
            retain_seconds=max(120, int((as_of - session_start).total_seconds()) + 5),
        )
        active = {ticker.upper()}
        corrections: list[RawBar] = []
        for event in events:
            if isinstance(event, dict):
                corrections.extend(builder.apply(event, active))
        one_second = [*corrections, *builder.flush(int(as_of.timestamp() * 1_000_000))]
        one_second.sort(key=lambda row: (row.bar_start_us, row.available_at_us))
        if not one_second:
            return []
        source_revision = str(revision_after.get("token") or revision_after.get("source_plan_hash") or "")
        one_second = [
            RawBar(
                ticker=row.ticker,
                view=row.view,
                bar_start_us=row.bar_start_us,
                bar_end_us=row.bar_end_us,
                available_at_us=row.available_at_us,
                values=row.values,
                revision=row.revision,
                source="qmd-history:compact-events",
                source_revision=source_revision,
            )
            for row in one_second
        ]
        view = _bar_view(one_second)
        result = list(one_second)
        for name, timeframe_us in INTRADAY_VIEW_US.items():
            if name == "1s":
                continue
            rolled = rollup_intraday_view(view, timeframe_us)
            result.extend(
                _view_rows(
                    ticker.upper(),
                    name,
                    rolled,
                    rolled.features,
                    "qmd-history:compact-events",
                    origin_us=int(as_of.timestamp() * 1_000_000),
                    past_limit=int(self.release.data_config.intraday_context_by_name[name]),
                )
            )
        return result


def _read_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _bar_view(rows: list[RawBar]) -> BarView:
    return BarView(
        features=torch.as_tensor([row.values for row in rows], dtype=torch.float32),
        bar_start_us=torch.as_tensor([row.bar_start_us for row in rows], dtype=torch.long),
        bar_end_us=torch.as_tensor([row.bar_end_us for row in rows], dtype=torch.long),
        available_at_us=torch.as_tensor(
            [row.available_at_us for row in rows], dtype=torch.long
        ),
    )


def _historical_window(as_of: dt.datetime) -> tuple[dt.date, dt.date, dt.date]:
    local_day = as_of.astimezone(NEW_YORK).date()
    return local_day, local_day - dt.timedelta(days=800), local_day + dt.timedelta(days=1)


def _bounded_event_revision_sql(
    database: str,
    ticker: str,
    history_start: dt.date,
    end: dt.date,
) -> tuple[str, str]:
    database_sql = _sql_text(database)
    ticker_sql = _sql_text(ticker.upper())
    start_sql = history_start.isoformat()
    end_sql = end.isoformat()
    ticker_days = f"""
SELECT source_date, event_count, first_ordinal, last_ordinal, next_ordinal,
       first_sip_timestamp_us, last_sip_timestamp_us, build_step, built_at
FROM `{database_sql}`.`events_ticker_day_index` FINAL
WHERE ticker = '{ticker_sql}'
  AND source_date >= toDate('{start_sql}')
  AND source_date < toDate('{end_sql}')
ORDER BY source_date
FORMAT JSONEachRow
"""
    source_days = f"""
SELECT source_date, stats_version, source_filter_key,
       quote_file_path, quote_file_size, quote_file_mtime_ns,
       trade_file_path, trade_file_size, trade_file_mtime_ns,
       quote_event_rows, trade_event_rows, total_event_rows_after_filters,
       first_sip_timestamp_us, last_sip_timestamp_us, updated_at
FROM `{database_sql}`.`events_source_day_stats` FINAL
WHERE source_date >= toDate('{start_sql}')
  AND source_date < toDate('{end_sql}')
ORDER BY source_date, stats_version, source_filter_key,
         quote_file_path, quote_file_size, quote_file_mtime_ns,
         trade_file_path, trade_file_size, trade_file_mtime_ns
FORMAT JSONEachRow
"""
    return ticker_days, source_days


def _sql_text(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _intraday_context_satisfied(
    sessions: list[tuple[str, BarView]],
    required: dict[str, int],
) -> bool:
    counts = {name: 0 for name in required}
    for _day, view in sessions:
        counts["1s"] = counts.get("1s", 0) + int(view.features.shape[0])
        for name, timeframe_us in INTRADAY_VIEW_US.items():
            if name != "1s" and name in counts:
                counts[name] += int(rollup_intraday_view(view, timeframe_us).features.shape[0])
    return all(counts.get(name, 0) >= int(size) for name, size in required.items())


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


def _view_rows(
    ticker: str,
    name: str,
    view: BarView,
    features: torch.Tensor,
    source: str,
    *,
    past_limit: int | None = None,
    origin_us: int | None = None,
) -> list[RawBar]:
    if past_limit is not None and origin_us is not None:
        past = torch.nonzero(view.available_at_us <= origin_us, as_tuple=False).flatten().tolist()
        future = torch.nonzero(view.available_at_us > origin_us, as_tuple=False).flatten().tolist()
        indices = [*past[-max(1, int(past_limit)):], *future]
    else:
        indices = range(features.shape[0])
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
        for index in indices
    ]
