from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v3.strategy import LongMomentumV3Strategy
from src.strategies.long_momentum.v8.config import LongMomentumV8Config
from src.strategies.long_momentum.v8.presentation import chart_presentation


REQUIRED_SHOCK_COLUMNS = (
    "price_shock_score",
    "volume_shock_score",
    "price_volume_shock_score",
    "confirmed_price_volume_shock",
    "shock_confirmation_type",
)


@dataclass(slots=True)
class NewsShockWatch:
    ticker: str
    seed_timestamp: datetime
    seed_minute_of_day: int | None
    seed_high: float
    seed_low: float
    seed_body_high: float
    seed_midpoint: float
    seed_score: float
    best_volume_score: float
    best_combined_score: float
    confirmation_seen: bool
    confirmation_type: str
    post_shock_high: float
    post_shock_low: float


class LongMomentumV8Strategy(LongMomentumV3Strategy):
    name = "long_momentum"

    def __init__(self, config: LongMomentumV8Config | None = None):
        super().__init__(config or LongMomentumV8Config())
        self.config: LongMomentumV8Config
        self.shock_watches: dict[str, NewsShockWatch] = {}
        self.entries_by_symbol: dict[str, int] = {}
        self.daily_entry_count = 0

    def data_requirements(self) -> DataRequirements:
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=("core", "momentum", "session", "volume_liquidity", "shock"),
            required_columns=(
                "ticker",
                "bar_time_market",
                "minute_of_day",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "transactions",
                "spread",
            ),
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> pl.DataFrame:
        self.shock_watches = {}
        self.entries_by_symbol = {}
        self.daily_entry_count = 0
        self.session_date = frames.session_date
        self._validate_provider_columns(frames.event_frame)
        return super().prepare_day(frames, portfolio)

    def _validate_provider_columns(self, frame: pl.DataFrame) -> None:
        missing = [column for column in REQUIRED_SHOCK_COLUMNS if column not in frame.columns]
        if missing:
            date_text = self.session_date.isoformat() if self.session_date else "unknown session"
            missing_text = ", ".join(missing)
            raise ValueError(
                f"Long Momentum v8 requires provider-built shock features for {date_text}; "
                f"missing columns: {missing_text}. Rebuild market data with the shock feature group."
            )

    def _update_states(self, context: BarContext) -> None:
        super()._update_states(context)
        for raw in context.updates.iter_rows(named=True):
            self._update_news_shock_watch(context.timestamp, dict(raw))

    def _update_news_shock_watch(self, timestamp: datetime, row: dict[str, Any]) -> None:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            return
        watch = self.shock_watches.get(ticker)
        if watch is not None and self._watch_age_minutes(timestamp, watch) > self.config.max_shock_watch_minutes:
            watch = None
            self.shock_watches.pop(ticker, None)

        if watch is not None:
            last_high = self._float(self._value(row, "high"))
            last_low = self._float(self._value(row, "low"))
            if last_high > 0:
                watch.post_shock_high = max(watch.post_shock_high, last_high)
            if last_low > 0:
                watch.post_shock_low = min(value for value in [watch.post_shock_low, last_low] if value > 0)
            volume_score = self._float(self._value(row, "volume_shock_score"))
            combined_score = self._float(self._value(row, "price_volume_shock_score"))
            watch.best_volume_score = max(watch.best_volume_score, volume_score)
            watch.best_combined_score = max(watch.best_combined_score, combined_score)
            if self._bool(self._value(row, "confirmed_price_volume_shock")) or self._bool(self._value(row, "volume_shock")):
                watch.confirmation_seen = True
                confirmation_type = str(self._value(row, "shock_confirmation_type") or "").strip()
                if confirmation_type and confirmation_type != "NONE":
                    watch.confirmation_type = confirmation_type

        if not self._shock_seed_open(row):
            return

        seed = self._build_watch(timestamp, ticker, row)
        if seed is None:
            return
        current = self.shock_watches.get(ticker)
        if current is None or seed.seed_score >= current.seed_score or self._watch_age_minutes(timestamp, current) >= 3:
            self.shock_watches[ticker] = seed

    def _build_watch(self, timestamp: datetime, ticker: str, row: dict[str, Any]) -> NewsShockWatch | None:
        high = self._float(self._value(row, "high"))
        low = self._float(self._value(row, "low"))
        open_price = self._float(self._value(row, "open"))
        close = self._float(self._value(row, "close"))
        if high <= 0 or low <= 0 or high < low or close <= 0:
            return None
        body_high = max(open_price, close) if open_price > 0 else close
        midpoint = (high + low) / 2.0
        price_score = self._float(self._value(row, "price_shock_score"))
        volume_score = self._float(self._value(row, "volume_shock_score"))
        combined_score = self._float(self._value(row, "price_volume_shock_score"))
        seed_score = max(combined_score, (price_score * 0.65) + (volume_score * 0.35))
        confirmation_type = str(self._value(row, "shock_confirmation_type") or "").strip()
        return NewsShockWatch(
            ticker=ticker,
            seed_timestamp=timestamp - timedelta(minutes=1),
            seed_minute_of_day=self._last_minute_of_day(row),
            seed_high=high,
            seed_low=low,
            seed_body_high=body_high,
            seed_midpoint=midpoint,
            seed_score=seed_score,
            best_volume_score=volume_score,
            best_combined_score=combined_score,
            confirmation_seen=self._bool(self._value(row, "confirmed_price_volume_shock")) or self._bool(self._value(row, "volume_shock")),
            confirmation_type=confirmation_type if confirmation_type and confirmation_type != "NONE" else "",
            post_shock_high=high,
            post_shock_low=low,
        )

    def _shock_seed_open(self, row: dict[str, Any]) -> bool:
        price_score = self._float(self._value(row, "price_shock_score"))
        combined_score = self._float(self._value(row, "price_volume_shock_score"))
        close_location = self._float(self._value(row, "close_location"))
        price_shock = self._bool(self._value(row, "price_shock"))
        bullish = self._float(self._value(row, "close")) > self._float(self._value(row, "open"))
        seed_score_ok = price_score >= self.config.min_seed_price_shock_score or combined_score >= self.config.min_seed_combined_shock_score
        if not (seed_score_ok and (price_shock or bullish) and close_location >= self.config.min_seed_close_location):
            return False
        if self.config.require_news_time_window and not self._near_news_window(self._last_minute_of_day(row)):
            return False
        return True

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        if context.updates.is_empty():
            return []
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        rows: list[dict] = []
        entry_rank = 0
        for raw in context.updates.iter_rows(named=True):
            row = dict(raw)
            ticker = str(row.get("ticker") or "").upper()
            row["ticker"] = ticker
            row["timestamp"] = context.timestamp
            row["session_date"] = self.session_date.isoformat() if self.session_date else ""
            row["price"] = self._float(row.get("last_close"))
            row["held_quantity"] = portfolio.positions[ticker].quantity if ticker in portfolio.positions else 0
            row["open_positions"] = len(portfolio.positions)
            enriched = self._evaluate_news_shock_row(context.timestamp, row)
            row.update(enriched)
            row["entry_open"] = bool(enriched["long_momentum_v8_entry_open"])
            row["long_momentum_entry_open"] = row["entry_open"]
            row["scanner_score"] = enriched["long_momentum_v8_score"]
            row["status"] = self._scanner_status(row, ticker, portfolio, pending_symbols)
            row["entry_state"] = "entry_open" if row["entry_open"] else row["long_momentum_v8_reject_reason"]
            rows.append(row)
        rows.sort(
            key=lambda item: (
                bool(item.get("entry_open")),
                self._float(item.get("scanner_score")),
                self._float(item.get("last_recent_dollar_volume_5")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            if row["entry_open"]:
                entry_rank += 1
                row["entry_rank"] = entry_rank
            else:
                row["entry_rank"] = None
        return rows

    def _evaluate_news_shock_row(self, timestamp: datetime, row: dict[str, Any]) -> dict[str, Any]:
        watch = self.shock_watches.get(str(row.get("ticker") or "").upper())
        age = self._watch_age_minutes(timestamp, watch) if watch is not None else None
        current_open = self._float(row.get("current_open"))
        last_close = self._float(row.get("last_close"))
        last_vwap = self._float(row.get("last_vwap"))
        volume_vs_avg = self._ratio(self._float(row.get("last_volume")), self._float(row.get("last_avg_volume_so_far")))
        volume_vs_recent_3 = self._ratio(self._float(row.get("last_volume")), self._float(row.get("last_volume_avg_3")))
        volume_score = self._float(row.get("last_volume_shock_score"))
        combined_score = self._float(row.get("last_price_volume_shock_score"))
        confirmation_seen = bool(watch and watch.confirmation_seen) or self._bool(row.get("last_confirmed_price_volume_shock"))
        liquidity_score = max(volume_score, watch.best_volume_score if watch else 0.0)
        shock_score = max(combined_score, watch.best_combined_score if watch else 0.0, watch.seed_score if watch else 0.0)

        spread_ok = self._spread_ok(row)
        base_ok = (
            self.config.min_price <= last_close <= self.config.max_price
            and self._float(row.get("last_volume")) >= self.config.min_volume
            and self._float(row.get("last_transactions")) >= self.config.min_transactions
            and spread_ok
            and self._float(row.get("last_spread_bps_abs")) <= self.config.max_spread_bps_abs
            and self._float(row.get("last_spread_bps_max")) <= self.config.max_spread_bps_max
            and self._float(row.get("last_quote_valid_ratio")) >= self.config.min_quote_valid_ratio
            and self._float(row.get("last_locked_or_crossed_count")) <= self.config.max_locked_or_crossed_count
            and self._float(row.get("last_recent_dollar_volume_5")) >= self.config.min_recent_dollar_volume_5
        )
        watch_ok = watch is not None and age is not None and self.config.min_shock_entry_delay_minutes <= age <= self.config.max_shock_watch_minutes
        liquidity_ok = (
            confirmation_seen
            and liquidity_score >= self.config.min_liquidity_volume_shock_score
            and shock_score >= self.config.min_liquidity_combined_shock_score
            and volume_vs_avg >= self.config.min_volume_vs_avg_so_far
            and volume_vs_recent_3 >= self.config.min_volume_vs_recent_3
        )
        midpoint = watch.seed_midpoint if watch else 0.0
        acceptance_level = midpoint * (1.0 + self.config.min_price_acceptance_above_midpoint_pct) if midpoint > 0 else 0.0
        above_vwap = last_vwap > 0 and current_open > last_vwap and last_close > last_vwap
        distance_above_vwap = ((current_open / last_vwap) - 1.0) if current_open > 0 and last_vwap > 0 else 0.0
        distance_from_midpoint = ((current_open / midpoint) - 1.0) if current_open > 0 and midpoint > 0 else 0.0
        day_open = self._float(row.get("last_day_open"))
        trend_ok = (
            self._bool(row.get("last_tema_open"))
            and self._float(row.get("last_macd_line")) > 0
            and self._float(row.get("last_macd_hist_z_since_open")) >= self.config.min_macd_hist_z_since_open
            and (day_open <= 0 or (current_open > day_open and last_close > day_open))
        )
        price_acceptance_ok = (
            current_open > 0
            and last_close > 0
            and above_vwap
            and current_open >= acceptance_level
            and last_close >= acceptance_level
            and distance_above_vwap <= self.config.max_distance_above_vwap_pct
            and distance_from_midpoint <= self.config.max_distance_from_shock_midpoint_pct
            and self._float(row.get("last_close_location")) >= self.config.min_close_location
            and self._float(row.get("last_bearish_volume_divergence_score")) < self.config.max_bearish_divergence_entry_score
        )
        trigger_level = self._entry_trigger_level(row, watch)
        entry_trigger = current_open >= trigger_level > 0
        score = self._news_shock_score(
            shock_score=shock_score,
            liquidity_score=liquidity_score,
            volume_vs_avg=volume_vs_avg,
            volume_vs_recent_3=volume_vs_recent_3,
            price_acceptance_ok=price_acceptance_ok,
            trend_ok=trend_ok,
            entry_trigger=entry_trigger,
            age=age,
        )
        initial_stop = self._news_shock_stop(row, watch, current_open)
        risk_pct = ((current_open - initial_stop) / current_open) if current_open > 0 and initial_stop > 0 else 1.0
        risk_ok = 0 < risk_pct <= self.config.max_initial_risk_pct
        entry_time_ok = self.config.entry_start_minute <= int(self._float(row.get("minute_of_day"))) < self.config.entry_end_minute
        entry_limit_ok = (
            self.daily_entry_count < self.config.max_entries_per_day
            and self.entries_by_symbol.get(str(row.get("ticker") or "").upper(), 0) < self.config.max_entries_per_symbol_per_day
        )
        entry_open = (
            base_ok
            and entry_time_ok
            and entry_limit_ok
            and watch_ok
            and liquidity_ok
            and price_acceptance_ok
            and trend_ok
            and entry_trigger
            and risk_ok
            and score >= self.config.min_entry_score
        )
        return {
            "long_momentum_v8_shock_watch_active": watch is not None,
            "long_momentum_v8_shock_watch_age_minutes": age,
            "long_momentum_v8_shock_seed_score": watch.seed_score if watch else 0.0,
            "long_momentum_v8_shock_best_volume_score": watch.best_volume_score if watch else 0.0,
            "long_momentum_v8_shock_best_combined_score": watch.best_combined_score if watch else 0.0,
            "long_momentum_v8_shock_confirmation_seen": confirmation_seen,
            "long_momentum_v8_shock_confirmation_type": watch.confirmation_type if watch else "",
            "long_momentum_v8_volume_vs_avg_so_far": volume_vs_avg,
            "long_momentum_v8_volume_vs_recent_3": volume_vs_recent_3,
            "long_momentum_v8_liquidity_ok": liquidity_ok,
            "long_momentum_v8_price_acceptance_ok": price_acceptance_ok,
            "long_momentum_v8_trend_ok": trend_ok,
            "long_momentum_v8_entry_time_ok": entry_time_ok,
            "long_momentum_v8_entry_limit_ok": entry_limit_ok,
            "long_momentum_v8_entry_trigger": entry_trigger,
            "long_momentum_v8_entry_trigger_level": trigger_level,
            "long_momentum_v8_stop_price": initial_stop,
            "long_momentum_v8_initial_risk_pct": risk_pct,
            "long_momentum_v8_score": score,
            "long_momentum_v8_entry_open": entry_open,
            "entry_trigger": "NEWS_SHOCK_LIQUIDITY_RECLAIM" if entry_trigger else "",
            "long_momentum_v8_reject_reason": self._v8_reject_reason(
                base_ok=base_ok,
                entry_time_ok=entry_time_ok,
                entry_limit_ok=entry_limit_ok,
                watch_ok=watch_ok,
                liquidity_ok=liquidity_ok,
                price_acceptance_ok=price_acceptance_ok,
                trend_ok=trend_ok,
                entry_trigger=entry_trigger,
                risk_ok=risk_ok,
                score=score,
            ),
        }

    def _entry_request(self, candidate: dict, context: BarContext, available_cash: float) -> OrderRequest | None:
        symbol = str(candidate["ticker"])
        if self.daily_entry_count >= self.config.max_entries_per_day:
            self._reject(context.timestamp, symbol, "daily_entry_limit", candidate)
            return None
        if self.entries_by_symbol.get(symbol, 0) >= self.config.max_entries_per_symbol_per_day:
            self._reject(context.timestamp, symbol, "symbol_entry_limit", candidate)
            return None
        request = super()._entry_request(candidate, context, available_cash)
        if request is None:
            return None
        self.daily_entry_count += 1
        self.entries_by_symbol[symbol] = self.entries_by_symbol.get(symbol, 0) + 1
        self.shock_watches.pop(symbol, None)
        request.reason = "LONG_MOMENTUM_V8"
        request.tag = (
            f"ENTRY|rule=LONG_MOMENTUM_V8|trigger=NEWS_SHOCK_LIQUIDITY_RECLAIM"
            f"|rank={candidate.get('entry_rank') or candidate.get('rank')}|qty={request.quantity}"
            f"|entry={self._float(request.limit_price):.2f}|stop={self._float(request.protective_stop_price):.2f}"
            f"|score={self._float(candidate.get('long_momentum_v8_score')):.1f}"
            f"|shock={self._float(candidate.get('long_momentum_v8_shock_best_combined_score')):.2f}"
            f"|age={self._float(candidate.get('long_momentum_v8_shock_watch_age_minutes')):.0f}"
        )
        return request

    def _initial_stop_price(self, row: dict, entry_price: float) -> float:
        stop = self._float(row.get("long_momentum_v8_stop_price"))
        if 0 < stop < entry_price:
            return stop
        return super()._initial_stop_price(row, entry_price)

    def _set_entry_metadata(self, symbol: str, row: dict, *, rank: int, score: float, stop_price: float) -> None:
        super()._set_entry_metadata(symbol, row, rank=rank, score=score, stop_price=stop_price)
        self.entry_order_metadata[symbol].update(
            {
                "entry_trigger": "NEWS_SHOCK_LIQUIDITY_RECLAIM",
                "shock_score": row.get("long_momentum_v8_shock_best_combined_score"),
                "shock_age_minutes": row.get("long_momentum_v8_shock_watch_age_minutes"),
            }
        )

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, entry_price: float, stop_price: float) -> None:
        super()._trace_entry(timestamp, candidate, quantity, entry_price, stop_price)
        if self.signal_events:
            self.signal_events[-1].update(
                {
                    "strategy_version": "v8",
                    "entry_trigger": "NEWS_SHOCK_LIQUIDITY_RECLAIM",
                    "news_shock_score": candidate.get("long_momentum_v8_score"),
                    "shock_watch_age_minutes": candidate.get("long_momentum_v8_shock_watch_age_minutes"),
                }
            )

    def _entry_trigger_level(self, row: dict[str, Any], watch: NewsShockWatch | None) -> float:
        if watch is None:
            return 0.0
        reclaim_multiplier = 1.0 + (max(0.0, self.config.min_reclaim_bps) / 10_000.0)
        last_high = self._float(row.get("last_high"))
        body_reclaim = max(watch.seed_body_high, watch.seed_midpoint) * reclaim_multiplier
        base_break = min(watch.post_shock_high, max(watch.seed_high, last_high)) * reclaim_multiplier
        if self._float(row.get("last_close")) >= watch.seed_midpoint:
            return min(level for level in [body_reclaim, base_break] if level > 0)
        return body_reclaim

    def _news_shock_stop(self, row: dict[str, Any], watch: NewsShockWatch | None, entry_price: float) -> float:
        if watch is None or entry_price <= 0:
            return 0.0
        vwap = self._float(row.get("last_vwap"))
        candidates = [
            watch.post_shock_low,
            watch.seed_midpoint,
            self._float(row.get("last_3_candle_low_price")),
            vwap * (1.0 - max(0.0, self.config.vwap_stop_buffer_pct)) if vwap > 0 else 0.0,
        ]
        valid = [value for value in candidates if 0 < value < entry_price]
        return max(0.01, max(valid)) if valid else max(0.01, entry_price - self.config.stop_offset_dollars)

    def _news_shock_score(
        self,
        *,
        shock_score: float,
        liquidity_score: float,
        volume_vs_avg: float,
        volume_vs_recent_3: float,
        price_acceptance_ok: bool,
        trend_ok: bool,
        entry_trigger: bool,
        age: float | None,
    ) -> float:
        score = min(25.0, max(0.0, shock_score) * 25.0)
        score += min(20.0, max(0.0, liquidity_score) * 12.0 + min(volume_vs_avg, 3.0) * 4.0 + min(volume_vs_recent_3, 2.0) * 2.0)
        score += 20.0 if price_acceptance_ok else 0.0
        score += 15.0 if trend_ok else 0.0
        score += 15.0 if entry_trigger else 0.0
        if age is not None and self.config.min_shock_entry_delay_minutes <= age <= 8:
            score += 5.0
        return min(100.0, score)

    def _v8_reject_reason(
        self,
        *,
        base_ok: bool,
        entry_time_ok: bool,
        entry_limit_ok: bool,
        watch_ok: bool,
        liquidity_ok: bool,
        price_acceptance_ok: bool,
        trend_ok: bool,
        entry_trigger: bool,
        risk_ok: bool,
        score: float,
    ) -> str:
        if not base_ok:
            return "base_liquidity_or_price"
        if not entry_time_ok:
            return "entry_time"
        if not entry_limit_ok:
            return "entry_limit"
        if not watch_ok:
            return "no_recent_news_shock_watch"
        if not liquidity_ok:
            return "liquidity_ramp"
        if not price_acceptance_ok:
            return "price_acceptance"
        if not trend_ok:
            return "trend"
        if not entry_trigger:
            return "reclaim_trigger"
        if not risk_ok:
            return "initial_risk"
        if score < self.config.min_entry_score:
            return "score"
        return "filtered"

    def _spread_ok(self, row: dict[str, Any]) -> bool:
        if row.get("long_momentum_spread_ok") is not None:
            return self._bool(row.get("long_momentum_spread_ok"))
        spread = self._float(row.get("last_spread"))
        close = self._float(row.get("last_close"))
        if close < 5.0:
            return spread <= self.config.max_spread_below_5 + 1e-9
        return spread <= self.config.max_spread_5_to_10 + 1e-9

    def _value(self, row: dict[str, Any], name: str) -> Any:
        last_name = f"last_{name}"
        if last_name in row:
            return row.get(last_name)
        return row.get(name)

    def _last_minute_of_day(self, row: dict[str, Any]) -> int | None:
        minute = row.get("minute_of_day")
        if minute is None:
            return None
        return int(self._float(minute)) - 1

    def _near_news_window(self, minute_of_day: int | None) -> bool:
        if minute_of_day is None:
            return False
        minute = minute_of_day % 60
        window = max(0, int(self.config.news_time_window_minutes))
        near_hour = minute <= window or minute >= 60 - window
        near_half_hour = self.config.include_half_hour_news_window and abs(minute - 30) <= window
        return near_hour or near_half_hour

    def _watch_age_minutes(self, timestamp: datetime, watch: NewsShockWatch | None) -> float:
        if watch is None:
            return 0.0
        return max(0.0, (timestamp - watch.seed_timestamp).total_seconds() / 60.0)

    def _ratio(self, numerator: float, denominator: float) -> float:
        if numerator <= 0 or denominator <= 0:
            return 0.0
        return numerator / denominator

    def _bool(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)


__all__ = ["LongMomentumV8Strategy"]
