from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v5.config import LongMomentumV5Config
from src.strategies.long_momentum.v5.presentation import chart_presentation


@dataclass
class LongMomentumV5SymbolState:
    ticker: str
    last_timestamp: datetime | None = None
    row: dict[str, Any] = field(default_factory=dict)
    setup_body_high: float = 0.0
    setup_low: float = 0.0
    setup_bar_index: int = -1
    setup_expires_bar: int = -1


REQUIRED_PROVIDER_COLUMNS = (
    "last_bearish_volume_divergence_score",
)


class LongMomentumV5Strategy:
    name = "long_momentum"

    def __init__(self, config: LongMomentumV5Config | None = None):
        self.config = config or LongMomentumV5Config()
        self.session_date = None
        self.states: dict[str, LongMomentumV5SymbolState] = {}
        self.entry_order_metadata: dict[str, dict] = {}
        self.position_meta: dict[str, dict] = {}
        self.live_rankings: list[dict] = []
        self.signal_events: list[dict] = []
        self.rejection_events: list[dict] = []
        self.scanner_snapshots: list[dict] = []
        self.observability: ObservabilityRecorder | None = None

    def set_observability(self, observability: ObservabilityRecorder) -> None:
        self.observability = observability

    def data_requirements(self) -> DataRequirements:
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=("core", "momentum", "session", "volume_liquidity"),
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
        self.session_date = frames.session_date
        self.states = {}
        self.entry_order_metadata = {}
        self.position_meta = {}
        self._validate_provider_columns(frames.event_frame)
        return frames.event_frame.filter(
            (pl.col("minute_of_day") >= self.config.trading_start_minute)
            & (pl.col("minute_of_day") < self.config.trading_end_minute)
        )

    def _validate_provider_columns(self, frame: pl.DataFrame) -> None:
        missing = [column for column in REQUIRED_PROVIDER_COLUMNS if column not in frame.columns]
        if missing:
            date_text = self.session_date.isoformat() if self.session_date else "unknown session"
            missing_text = ", ".join(missing)
            raise ValueError(
                f"Long Momentum v5 requires provider-built volume divergence features for {date_text}; "
                f"missing columns: {missing_text}. Rebuild market data with current volume_liquidity features."
            )

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        self._update_states(context)
        requests: list[OrderRequest] = []
        requests.extend(self._partial_residual_requests(context, portfolio))

        active_pending_orders = [order for order in pending_orders if order.status == "OPEN"]
        requests.extend(self._exit_requests(context, portfolio, active_pending_orders))

        rows = self._scanner_rows(context, portfolio, active_pending_orders)
        candidates = [row for row in rows if row["entry_open"]]
        self._record_scanner(context, rows, candidates, portfolio, active_pending_orders)

        blocked_symbols = {
            order.symbol for order in active_pending_orders if order.side == "BUY"
        } | {
            request.symbol for request in requests if request.side == "BUY"
        } | set(portfolio.positions)
        available_cash = max(0.0, portfolio.cash - self.config.cash_buffer_dollars)
        for request in requests:
            if request.side == "BUY":
                available_cash -= self._estimated_buy_cost(request.quantity, self._float(request.limit_price or request.stop_price))

        for candidate in candidates:
            symbol = str(candidate["ticker"])
            if symbol in blocked_symbols:
                continue
            request = self._entry_request(candidate, context, portfolio, available_cash)
            if request is None:
                continue
            requests.append(request)
            blocked_symbols.add(symbol)
            available_cash -= self._estimated_buy_cost(request.quantity, self._float(request.limit_price or request.stop_price))
            if available_cash <= 0:
                break
        return requests

    def on_day_end(self, timestamp: datetime, portfolio: Portfolio) -> list[OrderRequest]:
        return [
            OrderRequest(
                symbol=symbol,
                side="SELL",
                quantity=position.quantity,
                order_type="MARKET",
                reason="EOD",
                tag=f"EXIT|reason=EOD|held_symbol={symbol}",
            )
            for symbol, position in list(portfolio.positions.items())
        ]

    def entry_metadata(self, order: Order) -> dict:
        return self.entry_order_metadata.get(order.symbol, {})

    def artifacts(self) -> dict[str, list[dict]]:
        return {
            "scanner_snapshots": self.scanner_snapshots,
            "live_rankings": self.live_rankings,
            "signal_events": self.signal_events,
            "rejection_events": self.rejection_events,
        }

    def _update_states(self, context: BarContext) -> None:
        for raw in context.updates.iter_rows(named=True):
            ticker = str(raw["ticker"])
            state = self.states.get(ticker)
            if state is None:
                state = LongMomentumV5SymbolState(ticker=ticker)
                self.states[ticker] = state
            state.last_timestamp = context.timestamp
            state.row = dict(raw)

    def _partial_residual_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        for fill in context.recent_fills:
            remaining = self._partial_remaining(fill.get("tag"))
            if remaining <= 0:
                continue
            symbol = str(fill.get("symbol") or "").upper()
            if not symbol:
                continue
            row = context.updates_by_symbol.get(symbol) or context.latest_by_symbol.get(symbol)
            if row is None:
                continue
            open_price = self._bar_open(row)
            if open_price <= 0:
                continue
            side = str(fill.get("side") or "").upper()
            if side == "BUY":
                self._reject(context.timestamp, symbol, "partial_entry_rest_disabled", row)
                continue
            elif side == "SELL":
                position = portfolio.positions.get(symbol)
                if position is None:
                    continue
                quantity = min(remaining, position.quantity)
                if quantity <= 0:
                    continue
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=quantity,
                        order_type="LIMIT",
                        reason="PARTIAL_EXIT_REST",
                        limit_price=open_price,
                        allow_same_bar_fill=True,
                        tag=f"EXIT|reason=PARTIAL_EXIT_REST|qty={quantity}|open={open_price:.2f}|source_fill={fill.get('fill_id')}",
                    )
                )
        return requests

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        if context.updates.is_empty():
            return []

        names = set(context.updates.columns)

        def column(name: str, default: Any = None) -> pl.Expr:
            if name in names:
                return pl.col(name)
            return pl.lit(default)

        last_spread = column("last_spread", None).cast(pl.Float64)
        long_spread_ok = (
            column("long_momentum_spread_ok", None)
            if "long_momentum_spread_ok" in names
            else pl.when(column("last_close", None).cast(pl.Float64) < 5.0)
            .then(last_spread <= self.config.max_spread_below_5 + 1e-9)
            .otherwise(last_spread <= self.config.max_spread_5_to_10 + 1e-9)
        )
        tema_open = (
            column("last_tema_open", None)
            if "last_tema_open" in names
            else column("last_tema9", None).cast(pl.Float64) > column("last_tema20", None).cast(pl.Float64)
        )
        last_body_high = (
            column("last_body_high", None).cast(pl.Float64)
            if "last_body_high" in names
            else pl.max_horizontal(column("last_open", None).cast(pl.Float64), column("last_close", None).cast(pl.Float64))
        )
        last_body_low = (
            column("last_body_low", None).cast(pl.Float64)
            if "last_body_low" in names
            else pl.min_horizontal(column("last_open", None).cast(pl.Float64), column("last_close", None).cast(pl.Float64))
        )

        frame = context.updates.with_columns(
            column("last_close", None).cast(pl.Float64).alias("_lm_last_close"),
            column("last_volume", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_last_volume"),
            column("last_transactions", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_last_transactions"),
            column("last_recent_volume_5", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_recent_volume_5"),
            column("last_recent_dollar_volume_5", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_recent_dollar_volume_5"),
            column("last_spread_bps_abs", None).cast(pl.Float64).alias("_lm_spread_bps_abs"),
            column("last_spread_bps_max", None).cast(pl.Float64).alias("_lm_spread_bps_max"),
            column("last_quote_valid_ratio", None).cast(pl.Float64).alias("_lm_quote_valid_ratio"),
            column("last_locked_or_crossed_count", None).cast(pl.Float64).alias("_lm_locked_or_crossed_count"),
            column("last_macd_line", None).cast(pl.Float64).alias("_lm_macd_line"),
            column("last_macd_hist_z_since_open", None).cast(pl.Float64).alias("_lm_macd_hist_z"),
            column("last_quote_ask_size", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_quote_ask_size"),
            column("current_open", None).cast(pl.Float64).alias("_lm_current_open"),
            last_body_high.alias("_lm_last_body_high"),
            last_body_low.alias("_lm_last_body_low"),
            column("last_3_candle_low_price", None).cast(pl.Float64).alias("_lm_last_3_candle_low"),
            column("last_bearish_volume_divergence_score", None).cast(pl.Float64).fill_null(0.0).alias("_lm_bearish_divergence_score"),
            column("last_vwap", None).cast(pl.Float64).alias("_lm_last_vwap"),
            column("last_day_open", None).cast(pl.Float64).alias("_lm_day_open"),
            column("last_day_high_so_far", None).cast(pl.Float64).alias("_lm_day_high"),
            column("last_day_low_so_far", None).cast(pl.Float64).alias("_lm_day_low"),
            column("last_tema9", None).cast(pl.Float64).alias("_lm_tema9"),
            column("last_tema20", None).cast(pl.Float64).alias("_lm_tema20"),
            column("last_macd_hist", None).cast(pl.Float64).alias("_lm_macd_hist"),
            column("last_avg_volume_so_far", None).cast(pl.Float64).alias("_lm_avg_volume_so_far"),
            column("last_volume_avg_3", None).cast(pl.Float64).alias("_lm_volume_avg_3"),
            column("last_close_location", None).cast(pl.Float64).alias("_lm_close_location"),
            column("last_bar_range", None).cast(pl.Float64).alias("_lm_last_bar_range"),
            long_spread_ok.fill_null(False).alias("_lm_spread_ok"),
            tema_open.fill_null(False).alias("_lm_tema_open"),
        ).with_columns(
            ((pl.col("_lm_tema9") / pl.col("_lm_tema20")) - 1.0).fill_null(0.0).alias("long_momentum_v5_tema_spread_pct"),
            (pl.col("_lm_last_volume") / pl.col("_lm_avg_volume_so_far")).fill_null(0.0).alias("long_momentum_v5_volume_vs_avg_so_far"),
            (pl.col("_lm_last_volume") / pl.col("_lm_volume_avg_3")).fill_null(0.0).alias("long_momentum_v5_volume_vs_recent_3"),
            ((pl.col("_lm_current_open") / pl.col("_lm_last_vwap")) - 1.0).fill_null(0.0).alias("long_momentum_v5_distance_above_vwap_pct"),
            ((pl.col("_lm_current_open") / pl.col("_lm_day_low")) - 1.0).fill_null(0.0).alias("long_momentum_v5_distance_from_day_low_pct"),
            ((pl.col("_lm_current_open") / pl.col("_lm_last_close")) - 1.0).fill_null(0.0).alias("long_momentum_v5_open_above_last_close_pct"),
            (pl.col("_lm_last_bar_range") / pl.col("_lm_last_close")).fill_null(0.0).alias("long_momentum_v5_last_bar_range_pct"),
            (
                pl.col("_lm_current_open")
                > (pl.col("_lm_day_high") * (1.0 + max(0.0, self.config.fresh_day_high_break_bps) / 10_000.0))
            )
            .fill_null(False)
            .alias("long_momentum_v5_fresh_day_high_break"),
            (pl.col("_lm_current_open") >= (pl.col("_lm_day_high") * (1.0 - max(0.0, self.config.near_day_high_chase_pct))))
            .fill_null(False)
            .alias("long_momentum_v5_near_day_high"),
        ).with_columns(
            (pl.col("_lm_tema9") > pl.col("_lm_tema20")).fill_null(False).alias("long_momentum_v5_tema_stack_ok"),
            (pl.col("long_momentum_v5_tema_spread_pct") >= self.config.min_tema_spread_pct)
            .fill_null(False)
            .alias("long_momentum_v5_tema_spread_ok"),
            (pl.col("_lm_macd_line") > 0).fill_null(False).alias("long_momentum_v5_macd_line_positive"),
            ((pl.col("_lm_macd_hist") > 0) | (pl.col("_lm_macd_hist_z") >= self.config.min_macd_hist_z_since_open))
            .fill_null(False)
            .alias("long_momentum_v5_macd_hist_ok"),
            ((pl.col("_lm_current_open") > pl.col("_lm_day_open")) & (pl.col("_lm_last_close") > pl.col("_lm_day_open")))
            .fill_null(False)
            .alias("long_momentum_v5_above_day_open"),
            (pl.col("_lm_current_open") >= (pl.col("_lm_day_high") * (1.0 - max(0.0, self.config.max_entry_below_day_high_pct))))
            .fill_null(False)
            .alias("long_momentum_v5_near_enough_day_high"),
            (pl.col("long_momentum_v5_volume_vs_avg_so_far") >= self.config.min_volume_vs_avg_so_far)
            .fill_null(False)
            .alias("long_momentum_v5_volume_vs_avg_so_far_ok"),
            (pl.col("long_momentum_v5_volume_vs_recent_3") >= self.config.min_volume_vs_recent_3)
            .fill_null(False)
            .alias("long_momentum_v5_volume_vs_recent_3_ok"),
            (pl.col("_lm_bearish_divergence_score") < self.config.max_bearish_divergence_entry_score)
            .fill_null(False)
            .alias("long_momentum_v5_bearish_divergence_ok"),
            (pl.col("long_momentum_v5_distance_above_vwap_pct") <= self.config.max_distance_above_vwap_pct)
            .fill_null(False)
            .alias("long_momentum_v5_distance_above_vwap_ok"),
            (pl.col("long_momentum_v5_distance_from_day_low_pct") <= self.config.max_distance_from_day_low_pct)
            .fill_null(False)
            .alias("long_momentum_v5_distance_from_day_low_ok"),
            (pl.col("long_momentum_v5_open_above_last_close_pct") <= self.config.max_open_above_last_close_pct)
            .fill_null(False)
            .alias("long_momentum_v5_open_above_last_close_ok"),
            (pl.col("long_momentum_v5_last_bar_range_pct") <= self.config.max_last_bar_range_pct)
            .fill_null(False)
            .alias("long_momentum_v5_last_bar_range_ok"),
            (pl.col("_lm_close_location") >= self.config.min_close_location).fill_null(False).alias("long_momentum_v5_close_location_ok"),
        ).with_columns(
            (
                (pl.col("_lm_current_open") > pl.col("_lm_last_vwap"))
                & (pl.col("_lm_last_close") > pl.col("_lm_last_vwap"))
            )
            .fill_null(False)
            .alias("long_momentum_v5_price_above_vwap"),
            (
                pl.col("_lm_tema_open")
                & pl.col("long_momentum_v5_tema_stack_ok")
                & pl.col("long_momentum_v5_tema_spread_ok")
                & pl.col("long_momentum_v5_macd_line_positive")
                & (pl.col("_lm_macd_hist") > 0)
                & (pl.col("_lm_macd_hist_z") >= self.config.min_macd_hist_z_since_open)
                & pl.col("long_momentum_v5_above_day_open")
            )
            .fill_null(False)
            .alias("long_momentum_v5_trend_quality_ok"),
            (
                pl.col("long_momentum_v5_volume_vs_avg_so_far_ok")
                & pl.col("long_momentum_v5_volume_vs_recent_3_ok")
            )
            .fill_null(False)
            .alias("long_momentum_v5_volume_expansion_ok"),
        ).with_columns(
            (
                ~pl.col("long_momentum_v5_near_day_high")
                | pl.col("long_momentum_v5_fresh_day_high_break")
                | (pl.col("long_momentum_v5_trend_quality_ok") & pl.col("long_momentum_v5_volume_expansion_ok"))
            )
            .fill_null(False)
            .alias("long_momentum_v5_day_high_chase_ok"),
            (
                pl.col("long_momentum_v5_near_enough_day_high")
                & (
                    ~pl.col("long_momentum_v5_near_day_high")
                    | pl.col("long_momentum_v5_fresh_day_high_break")
                )
            )
            .fill_null(False)
            .alias("long_momentum_v5_day_high_position_ok"),
        ).with_columns(
            (
                pl.col("long_momentum_v5_distance_above_vwap_ok")
                & pl.col("long_momentum_v5_distance_from_day_low_ok")
                & pl.col("long_momentum_v5_open_above_last_close_ok")
                & pl.col("long_momentum_v5_last_bar_range_ok")
                & pl.col("long_momentum_v5_close_location_ok")
                & pl.col("long_momentum_v5_day_high_position_ok")
            )
            .fill_null(False)
            .alias("long_momentum_v5_early_move_ok"),
        ).with_columns(
            (
                (pl.col("_lm_last_close") >= self.config.min_price)
                & (pl.col("_lm_last_close") <= self.config.max_price)
                & (pl.col("_lm_last_volume") >= self.config.min_volume)
                & (pl.col("_lm_last_transactions") >= self.config.min_transactions)
                & pl.col("_lm_spread_ok")
                & (pl.col("_lm_recent_dollar_volume_5") >= self.config.min_recent_dollar_volume_5)
                & (pl.col("_lm_spread_bps_abs") <= self.config.max_spread_bps_abs)
                & (pl.col("_lm_spread_bps_max") <= self.config.max_spread_bps_max)
                & (pl.col("_lm_quote_valid_ratio") >= self.config.min_quote_valid_ratio)
                & (pl.col("_lm_locked_or_crossed_count") <= self.config.max_locked_or_crossed_count)
                & pl.col("long_momentum_v5_bearish_divergence_ok")
                & pl.col("long_momentum_v5_price_above_vwap")
                & pl.col("long_momentum_v5_trend_quality_ok")
                & pl.col("long_momentum_v5_volume_expansion_ok")
                & pl.col("long_momentum_v5_early_move_ok")
            ).fill_null(False).alias("setup_open"),
            (pl.col("_lm_current_open") > pl.col("_lm_last_body_high")).fill_null(False).alias("body_break_entry_open"),
            pl.col("_lm_last_body_high").alias("last_body_high"),
            pl.col("_lm_last_body_low").alias("last_body_low"),
            pl.col("_lm_spread_ok").alias("long_momentum_spread_ok"),
            pl.col("_lm_recent_volume_5").alias("scanner_score"),
        )

        rows = frame.to_dicts()
        self._annotate_entry_triggers(context, rows)
        rows = sorted(
            rows,
            key=lambda row: (
                bool(row.get("entry_open")),
                self._float(row.get("scanner_score")),
                self._float(row.get("_lm_recent_dollar_volume_5")),
            ),
            reverse=True,
        )
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        entry_rank = 0
        for rank, row in enumerate(rows, start=1):
            ticker = str(row["ticker"])
            row["timestamp"] = context.timestamp
            row["session_date"] = self.session_date.isoformat() if self.session_date else ""
            row["ticker"] = ticker
            row["price"] = self._float(row.get("last_close"))
            row["rank"] = rank
            row["held_quantity"] = portfolio.positions[ticker].quantity if ticker in portfolio.positions else 0
            row["open_positions"] = len(portfolio.positions)
            row["status"] = self._scanner_status(row, ticker, portfolio, pending_symbols)
            row["entry_state"] = "entry_open" if row["entry_open"] else self._entry_block_reason(row)
            if row["entry_open"]:
                entry_rank += 1
                row["entry_rank"] = entry_rank
            else:
                row["entry_rank"] = None
        return rows

    def _annotate_entry_triggers(self, context: BarContext, rows: list[dict]) -> None:
        bar_index = self._bar_index(context)
        for row in rows:
            symbol = str(row.get("ticker") or "").upper()
            state = self.states.get(symbol)
            setup_open = bool(row.get("setup_open"))
            current_open = self._float(row.get("current_open"))
            body_high = self._float(row.get("_lm_last_body_high"))

            active_setup = self._has_active_setup(state, bar_index)
            active_setup_high = state.setup_body_high if active_setup and state is not None else 0.0
            body_break_threshold = max(body_high, active_setup_high)
            entry_threshold = body_break_threshold * (1.0 + max(0.0, self.config.min_body_break_bps) / 10_000.0)

            early_uptrend_entry = (
                setup_open
                and self.config.enable_early_uptrend_entry
                and self._entry_time_ok(context)
                and current_open > 0
                and body_break_threshold > 0
                and current_open > entry_threshold
                and current_open >= self._float(row.get("last_close"))
            )
            entry_open = early_uptrend_entry
            trigger = "early_uptrend_break" if early_uptrend_entry else ""

            setup_low = self._setup_stop_low(row, current_open)
            if setup_open and state is not None:
                if not active_setup or body_high > state.setup_body_high:
                    state.setup_body_high = body_high
                    state.setup_low = setup_low
                    state.setup_bar_index = bar_index
                    state.setup_expires_bar = bar_index + max(1, int(self.config.setup_valid_bars))
                elif setup_low > 0:
                    state.setup_low = min(value for value in (state.setup_low, setup_low) if value > 0)

            display_active_setup = self._has_active_setup(state, bar_index)
            display_active_setup_high = state.setup_body_high if display_active_setup and state is not None else 0.0
            row["setup_stop_low"] = state.setup_low if display_active_setup and state and state.setup_low > 0 else setup_low
            row["setup_body_high"] = display_active_setup_high if display_active_setup_high > 0 else body_high
            row["active_setup_body_high"] = display_active_setup_high if display_active_setup_high > 0 else None
            row["body_break_threshold"] = body_break_threshold
            row["trigger_1_threshold"] = entry_threshold
            row["trigger_1_time_ok"] = self._entry_time_ok(context)
            row["long_momentum_v5_entry_threshold"] = entry_threshold
            row["long_momentum_v5_entry_time_ok"] = self._entry_time_ok(context)
            row["long_momentum_setup_open"] = setup_open
            row["long_momentum_v5_setup_open"] = setup_open
            row["long_momentum_body_break_entry_open"] = early_uptrend_entry
            row["long_momentum_early_body_break_entry_open"] = early_uptrend_entry
            row["long_momentum_pullback_reclaim_entry_open"] = False
            row["long_momentum_v5_early_uptrend_entry_open"] = early_uptrend_entry
            row["long_momentum_v5_body_break_entry_open"] = early_uptrend_entry
            row["long_momentum_v5_pullback_reclaim_entry_open"] = False
            row["body_break_entry_open"] = early_uptrend_entry
            row["pullback_reclaim_entry_open"] = False
            row["entry_trigger"] = trigger
            row["long_momentum_entry_trigger"] = trigger
            row["entry_open"] = entry_open
            row["long_momentum_entry_open"] = entry_open
            row["long_momentum_v5_entry_open"] = entry_open

    def _has_active_setup(self, state: LongMomentumV5SymbolState | None, bar_index: int) -> bool:
        return bool(
            state is not None
            and state.setup_body_high > 0
            and state.setup_bar_index < bar_index <= state.setup_expires_bar
        )

    def _entry_time_ok(self, context: BarContext) -> bool:
        minute = self._minute_of_day(context)
        if minute is None:
            return False
        primary = self.config.entry_minute_start <= minute < self.config.entry_minute_end
        late = self.config.entry_late_minute_start <= minute < self.config.entry_late_minute_end
        return primary or late

    def _setup_stop_low(self, row: dict, entry_price: float) -> float:
        candidates = [
            self._float(row.get("last_3_candle_low_price")),
            self._float(row.get("_lm_last_body_low")),
            min(self._float(row.get("last_open")), self._float(row.get("last_close"))),
        ]
        valid = [value for value in candidates if value > 0 and (entry_price <= 0 or value < entry_price)]
        return min(valid) if valid else 0.0

    def _entry_request(self, candidate: dict, context: BarContext, portfolio: Portfolio, available_cash: float) -> OrderRequest | None:
        symbol = str(candidate["ticker"])
        entry_price = self._float(candidate.get("current_open"))
        ask_size = int(self._float(candidate.get("last_quote_ask_size")))
        if entry_price <= 0 or ask_size <= 0:
            self._reject(context.timestamp, symbol, "quote_ask_size", candidate)
            return None
        stop_price = self._initial_stop_price(candidate, entry_price)
        risk_pct = (entry_price - stop_price) / entry_price if entry_price > 0 else 0.0
        if risk_pct <= 0 or risk_pct > self.config.max_initial_risk_pct:
            self._reject(context.timestamp, symbol, "initial_risk", candidate)
            return None
        cash_quantity = self._cash_quantity(entry_price, available_cash)
        risk_quantity = self._risk_quantity(entry_price, stop_price, portfolio.total_equity(context.latest_by_symbol))
        quantity = min(ask_size, cash_quantity, risk_quantity)
        if quantity <= 0:
            self._reject(context.timestamp, symbol, "risk_or_cash", candidate)
            return None
        rank = int(candidate.get("entry_rank") or candidate.get("rank") or 0)
        score = self._float(candidate.get("scanner_score"))
        self._set_entry_metadata(symbol, candidate, rank=rank, score=score, stop_price=stop_price)
        self.position_meta[symbol] = {
            "initial_stop": stop_price,
            "initial_r": max(self.config.stop_offset_dollars, abs(entry_price - stop_price)),
            "entry_score": score,
            "exit_watch_active": False,
            "exit_watch_stop": 0.0,
            "exit_watch_score": 0.0,
        }
        self._trace_entry(context.timestamp, candidate, quantity, entry_price, stop_price)
        return OrderRequest(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="LIMIT",
            reason="LONG_MOMENTUM_V5",
            limit_price=entry_price,
            allow_same_bar_fill=True,
            protective_stop_price=stop_price,
            tag=(
                f"ENTRY|rule=LONG_MOMENTUM_V5|trigger={candidate.get('entry_trigger') or 'unknown'}"
                f"|rank={rank}|qty={quantity}|entry={entry_price:.2f}"
                f"|stop={stop_price:.2f}|last_recent_volume_5={score:.0f}|ask_size={ask_size}"
                f"|risk_qty={risk_quantity}|cash_qty={cash_quantity}"
                f"|macdz={self._float(candidate.get('last_macd_hist_z_since_open')):.2f}"
            ),
        )

    def _exit_requests(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        pending_sell_symbols = {order.symbol for order in pending_orders if order.side == "SELL" and order.status == "OPEN"}
        for symbol, position in list(portfolio.positions.items()):
            if symbol in pending_sell_symbols:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            meta = self._position_meta(symbol, position)
            if self._definite_bearish_divergence_close(bar):
                self._trace_exit(context.timestamp, symbol, "BEARISH_VOLUME_DIVERGENCE_CLOSE", position, bar, meta)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="BEARISH_VOLUME_DIVERGENCE_CLOSE",
                        tag=self._exit_tag("BEARISH_VOLUME_DIVERGENCE_CLOSE", position, bar, meta),
                    )
                )
                continue
            trend_failure = self._trend_failure_exit_reason(position, bar)
            if trend_failure:
                self._trace_exit(context.timestamp, symbol, trend_failure, position, bar, meta)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason=trend_failure,
                        tag=self._exit_tag(trend_failure, position, bar, meta),
                    )
                )
                continue
            stop_price = self._managed_stop_price(symbol, position, bar, meta)
            if self._float(bar.get("last_bearish_volume_divergence_score")) >= self.config.exit_watch_bearish_divergence_score:
                stop_reason = "BEARISH_VOLUME_DIVERGENCE_WATCH"
            elif stop_price > position.stop_price + 1e-9:
                stop_reason = "STRUCTURAL_TREND_TRAIL"
            else:
                stop_reason = "INITIAL_STOP"
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="STOP",
                    reason=stop_reason,
                    stop_price=stop_price,
                    tag=self._exit_tag(stop_reason, position, bar, meta),
                    allow_same_bar_fill=True,
                    expire_on_bar_close=True,
                )
            )
        return requests

    def _set_entry_metadata(self, symbol: str, row: dict, *, rank: int, score: float, stop_price: float) -> None:
        self.entry_order_metadata[symbol] = {
            "setup_rank": rank,
            "live_rank": rank,
            "setup_score": score,
            "live_score": score,
            "stop_price": stop_price,
        }

    def _initial_stop_price(self, row: dict, entry_price: float) -> float:
        candidates = [
            self._float(row.get("setup_stop_low")),
            self._float(row.get("last_3_candle_low_price")),
            min(self._float(row.get("last_open")), self._float(row.get("last_close"))),
            self._float(row.get("last_vwap")) * (1.0 - max(0.0, self.config.vwap_stop_buffer_pct)),
        ]
        valid = [value for value in candidates if value > 0 and value < entry_price]
        if valid:
            return max(0.01, min(valid))
        return max(0.01, entry_price - self.config.stop_offset_dollars)

    def _cash_quantity(self, price: float, available_cash: float) -> int:
        if price <= 0 or available_cash <= 0:
            return 0
        per_share_cost = price + max(0.0, self.config.sizing_fee_per_share)
        quantity = int((available_cash - self.config.sizing_min_fee) / per_share_cost) if per_share_cost > 0 else 0
        while quantity > 0 and self._estimated_buy_cost(quantity, price) > available_cash:
            quantity -= 1
        return max(0, quantity)

    def _risk_quantity(self, entry_price: float, stop_price: float, equity: float) -> int:
        if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
            return 0
        risk_pct = max(0.0, self.config.risk_per_trade_pct)
        if risk_pct <= 0 or equity <= 0:
            return 10**12
        risk_per_share = entry_price - stop_price
        return max(0, int((equity * risk_pct) / risk_per_share))

    def _estimated_buy_cost(self, quantity: int, price: float) -> float:
        if quantity <= 0 or price <= 0:
            return 0.0
        fee = max(self.config.sizing_min_fee, quantity * self.config.sizing_fee_per_share)
        return quantity * price + fee

    def _definite_bearish_divergence_close(self, bar: dict) -> bool:
        return self._float(bar.get("last_bearish_volume_divergence_score")) >= self.config.exit_definite_bearish_divergence_score

    def _trend_failure_exit_reason(self, position, bar: dict) -> str | None:
        if self.config.trend_failure_requires_profit and position.max_r_multiple < self.config.breakeven_activation_r:
            return None
        last_close = self._float(bar.get("last_close"))
        if last_close <= 0:
            return None
        vwap = self._float(bar.get("last_vwap"))
        tema9 = self._float(bar.get("last_tema9"))
        tema20 = self._float(bar.get("last_tema20"))
        macd_hist = self._float(bar.get("last_macd_hist"))
        if vwap > 0 and last_close < vwap and position.max_r_multiple >= self.config.structural_trail_activation_r:
            return "VWAP_TREND_FAILURE"
        if tema9 > 0 and tema20 > 0 and tema9 < tema20:
            return "TEMA_TREND_FAILURE"
        if macd_hist < 0 and vwap > 0 and last_close <= vwap:
            return "MACD_VWAP_FAILURE"
        return None

    def _managed_stop_price(self, symbol: str, position, bar: dict, meta: dict) -> float:
        score = self._float(bar.get("last_bearish_volume_divergence_score"))
        last_close = self._float(bar.get("last_close"))
        stop = position.stop_price
        if position.max_r_multiple >= self.config.breakeven_activation_r:
            stop = max(stop, position.entry_price)
        if position.max_r_multiple >= self.config.structural_trail_activation_r:
            trail_candidates = [
                self._float(bar.get("last_3_candle_low_price")),
                self._float(bar.get("last_tema20")),
                self._float(bar.get("last_vwap")) * (1.0 - max(0.0, self.config.vwap_stop_buffer_pct)),
            ]
            valid_trails = [value for value in trail_candidates if value > 0 and value < last_close]
            if valid_trails:
                stop = max(stop, max(valid_trails))
        if (
            self.config.exit_watch_bearish_divergence_score <= score < self.config.exit_definite_bearish_divergence_score
            and last_close > position.entry_price
        ):
            meta["exit_watch_active"] = True
            meta["exit_watch_score"] = max(self._float(meta.get("exit_watch_score")), score)
            meta["exit_watch_stop"] = max(self._float(meta.get("exit_watch_stop")), last_close)
            self.position_meta[symbol] = meta
        if bool(meta.get("exit_watch_active")):
            watch_stop = self._float(meta.get("exit_watch_stop"))
            if last_close > watch_stop:
                watch_stop = last_close
                meta["exit_watch_stop"] = watch_stop
                self.position_meta[symbol] = meta
            if watch_stop > 0:
                stop = max(stop, watch_stop)
        return stop

    def _position_meta(self, symbol: str, position) -> dict:
        meta = self.position_meta.get(symbol)
        if meta is None:
            risk = max(self.config.stop_offset_dollars, abs(position.entry_price - position.stop_price))
            meta = {
                "initial_stop": position.stop_price,
                "initial_r": risk,
                "entry_score": position.live_score,
                "exit_watch_active": False,
                "exit_watch_stop": 0.0,
                "exit_watch_score": 0.0,
            }
            self.position_meta[symbol] = meta
        return meta

    def _scanner_status(self, row: dict, ticker: str, portfolio: Portfolio, pending_symbols: set[str]) -> str:
        if ticker in portfolio.positions:
            return "held"
        if ticker in pending_symbols:
            return "pending"
        return "eligible" if row.get("entry_open") else "blocked"

    def _entry_block_reason(self, row: dict) -> str:
        close = self._float(row.get("last_close"))
        if close < self.config.min_price:
            return "price_low"
        if close > self.config.max_price:
            return "price_high"
        if self._float(row.get("last_volume")) < self.config.min_volume:
            return "volume"
        if self._float(row.get("last_transactions")) < self.config.min_transactions:
            return "transactions"
        if not bool(row.get("_lm_spread_ok")):
            return "spread"
        if not bool(row.get("_lm_tema_open")):
            return "tema_closed"
        if self._float(row.get("last_macd_line")) <= 0:
            return "macd_line"
        if self._float(row.get("last_macd_hist_z_since_open")) < self.config.min_macd_hist_z_since_open:
            return "macd_hist_z"
        if self._float(row.get("last_recent_dollar_volume_5")) < self.config.min_recent_dollar_volume_5:
            return "recent_dollar_volume_5"
        if self._float(row.get("last_spread_bps_abs")) > self.config.max_spread_bps_abs:
            return "spread_bps_abs"
        if self._float(row.get("last_spread_bps_max")) > self.config.max_spread_bps_max:
            return "spread_bps_max"
        if self._float(row.get("last_quote_valid_ratio")) < self.config.min_quote_valid_ratio:
            return "quote_valid_ratio"
        if self._float(row.get("last_locked_or_crossed_count")) > self.config.max_locked_or_crossed_count:
            return "locked_or_crossed"
        if self._float(row.get("last_bearish_volume_divergence_score")) >= self.config.max_bearish_divergence_entry_score:
            return "bearish_volume_divergence"
        if not bool(row.get("long_momentum_v5_price_above_vwap")):
            return "not_above_vwap"
        if not bool(row.get("long_momentum_v5_trend_quality_ok")):
            return "trend_quality"
        if not bool(row.get("long_momentum_v5_volume_expansion_ok")):
            return "volume_expansion"
        if not bool(row.get("long_momentum_v5_day_high_position_ok")):
            return "day_high_position"
        if not bool(row.get("long_momentum_v5_early_move_ok")):
            return "extended_or_chasing"
        if not bool(row.get("long_momentum_v5_entry_time_ok")):
            return "entry_time"
        if not bool(row.get("long_momentum_v5_early_uptrend_entry_open")):
            return "entry_trigger"
        return "filtered"

    def _record_scanner(
        self,
        context: BarContext,
        rows: list[dict],
        candidates: list[dict],
        portfolio: Portfolio,
        pending_orders: list[Order],
    ) -> None:
        captured = rows[:25]
        self.live_rankings.extend(captured)
        self.scanner_snapshots.append(
            {
                "timestamp": context.timestamp,
                "session_date": self.session_date.isoformat() if self.session_date else "",
                "candidate_count": len(candidates),
                "scanned_count": len(rows),
                "selected_count": len(candidates),
            }
        )
        if not self.observability or not rows:
            return
        self.observability.scanner(timestamp=context.timestamp, rows=rows, score_key="scanner_score", stage="long_momentum_v5_scanner")
        self.observability.state(
            timestamp=context.timestamp,
            scope="strategy",
            state={
                "scanned_count": len(rows),
                "entry_open_count": len(candidates),
                "open_positions": len(portfolio.positions),
                "pending_orders": len([order for order in pending_orders if order.status == "OPEN"]),
            },
        )

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, entry_price: float, stop_price: float) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "recent_volume_5": candidate.get("last_recent_volume_5"),
                "quantity": quantity,
                "entry": entry_price,
                "stop": stop_price,
                "entry_trigger": candidate.get("entry_trigger"),
            }
        )
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=str(candidate["ticker"]),
            stage="order_request",
            event_type="entry_intent",
            decision="submit_order",
            reason_code="LONG_MOMENTUM_V5",
            reason="Eligible Long Momentum v5 scanner candidate",
            values={
                "quantity": quantity,
                "current_open": candidate.get("current_open"),
                "stop": stop_price,
                "entry_trigger": candidate.get("entry_trigger"),
                "setup_body_high": candidate.get("setup_body_high"),
                "setup_stop_low": candidate.get("setup_stop_low"),
                "last_recent_volume_5": candidate.get("last_recent_volume_5"),
                "last_recent_dollar_volume_5": candidate.get("last_recent_dollar_volume_5"),
                "last_quote_ask_size": candidate.get("last_quote_ask_size"),
                "last_macd_line": candidate.get("last_macd_line"),
                "last_macd_hist_z_since_open": candidate.get("last_macd_hist_z_since_open"),
                "long_momentum_v5_tema_spread_pct": candidate.get("long_momentum_v5_tema_spread_pct"),
                "long_momentum_v5_volume_vs_avg_so_far": candidate.get("long_momentum_v5_volume_vs_avg_so_far"),
                "long_momentum_v5_distance_above_vwap_pct": candidate.get("long_momentum_v5_distance_above_vwap_pct"),
                "long_momentum_v5_distance_from_day_low_pct": candidate.get("long_momentum_v5_distance_from_day_low_pct"),
                "long_momentum_v5_entry_threshold": candidate.get("long_momentum_v5_entry_threshold"),
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
            },
            force=self._force_trade_trace(),
        )

    def _trace_exit(self, timestamp: datetime, symbol: str, reason: str, position, bar: dict, meta: dict) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=symbol,
            stage="exit_evaluation",
            event_type="exit_intent",
            decision="exit",
            reason_code=reason,
            reason=f"Exit condition {reason} triggered",
            values={
                "quantity": position.quantity,
                "last_close": bar.get("last_close"),
                "last_bearish_volume_divergence_score": bar.get("last_bearish_volume_divergence_score"),
                "entry_price": position.entry_price,
                "initial_stop": meta.get("initial_stop"),
                "exit_watch_stop": meta.get("exit_watch_stop"),
                "exit_watch_score": meta.get("exit_watch_score"),
                "initial_r": meta.get("initial_r"),
            },
            force=self._force_trade_trace(),
        )

    def _reject(self, timestamp: datetime, symbol: str, reason: str, candidate: dict) -> None:
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": symbol,
                "reject_reason": reason,
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "last_recent_volume_5": candidate.get("last_recent_volume_5"),
            }
        )

    def _exit_tag(self, reason: str, position, bar: dict | None, meta: dict) -> str:
        price = self._float(bar.get("last_close")) if bar is not None else position.entry_price
        stop = self._float(meta.get("initial_stop")) or position.stop_price
        divergence_score = self._float(bar.get("last_bearish_volume_divergence_score")) if bar is not None else 0.0
        watch_stop = self._float(meta.get("exit_watch_stop"))
        return (
            f"EXIT|reason={reason}|price={price:.2f}|stop={stop:.2f}"
            f"|R={self._float(meta.get('initial_r')):.4f}|maxp={position.max_price:.2f}"
            f"|maxR={position.max_r_multiple:.2f}|bvd={divergence_score:.2f}|watch_stop={watch_stop:.2f}"
        )

    def _partial_remaining(self, tag: Any) -> int:
        parsed = self._parse_pipe_tag(str(tag or ""))
        try:
            return max(0, int(float(parsed.get("remaining", 0))))
        except (TypeError, ValueError):
            return 0

    def _parse_pipe_tag(self, tag: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for part in tag.split("|"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip().lower()] = value.strip()
        return values

    def _bar_open(self, row: dict) -> float:
        return self._float(row.get("current_open") if row.get("current_open") is not None else row.get("open"))

    def _bar_index(self, context: BarContext) -> int:
        for name in ("last_session_bar_count", "session_bar_count"):
            if name in context.updates.columns:
                series = context.updates.get_column(name)
                if len(series) > 0:
                    return int(self._float(series[0]))
        return 0

    def _minute_of_day(self, context: BarContext) -> int | None:
        if "minute_of_day" in context.updates.columns:
            series = context.updates.get_column("minute_of_day")
            if len(series) > 0:
                return int(self._float(series[0]))
        return None

    def _float(self, value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _force_trade_trace(self) -> bool:
        return bool(self.observability and self.observability.config.observability_always_trace_trades)

