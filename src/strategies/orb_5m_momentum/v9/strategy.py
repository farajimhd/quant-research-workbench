from __future__ import annotations

from datetime import datetime

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.strategies.orb_5m_momentum.v9.config import OrbMomentumConfig
from src.strategies.orb_5m_momentum.v9.presentation import chart_presentation


BLOCKED_SYMBOLS = {
    "KWEB",
    "YINN",
    "CWEB",
    "NUGT",
    "JNUG",
    "GDXU",
    "AGQ",
    "SCO",
    "TNA",
    "URTY",
    "DRN",
    "CONL",
    "ARKG",
    "MSOS",
    "URA",
}
BLOCKED_SUFFIXES = (".U", ".WS", ".WT", ".W", ".P", ".PR", ".R", "-U", "-WS", "-WT", "-W", "-P", "-PR", "-R")


class OrbFiveMinuteMomentumV9Strategy:
    name = "orb_5m_momentum"

    def __init__(self, config: OrbMomentumConfig | None = None):
        self.config = config or OrbMomentumConfig()
        self.session_date = None
        self.watchlist: dict[str, dict] = {}
        self.pending_candidates: list[dict] = []
        self.active_symbol: str | None = None
        self.entry_order_metadata: dict[str, dict] = {}
        self.scanner_snapshots: list[dict] = []
        self.candidate_rankings: list[dict] = []
        self.live_rankings: list[dict] = []
        self.signal_events: list[dict] = []
        self.rejection_events: list[dict] = []
        self.observability: ObservabilityRecorder | None = None

    def set_observability(self, observability: ObservabilityRecorder) -> None:
        self.observability = observability

    def data_requirements(self) -> DataRequirements:
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=("core", "session", "momentum"),
            daily_lookback_days=self.config.daily_lookback_days,
            daily_feature_groups=("volatility",),
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
            ),
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> pl.DataFrame:
        self.session_date = frames.session_date
        self.watchlist = {}
        self.pending_candidates = []
        self.active_symbol = None
        self.entry_order_metadata = {}

        setup_df = self._build_setup_dataframe(frames)
        candidates = setup_df.filter(pl.col("passes_setup_filter")).sort("orb_relative_volume", descending=True)
        selected = candidates.head(self.config.max_candidates)
        self._observe_setup_scan(frames, setup_df, candidates, selected, portfolio)

        self.scanner_snapshots.append(
            {
                "session_date": frames.session_date.isoformat(),
                "timestamp": self.opening_range_available_timestamp(frames.session_date),
                "candidate_count": candidates.height,
                "selected_count": selected.height,
                "watchlist_size": self.config.max_candidates,
            }
        )

        for rank, row in enumerate(selected.iter_rows(named=True), start=1):
            record = dict(row)
            record["session_date"] = frames.session_date.isoformat()
            record["rank"] = rank
            record["trigger"] = self.entry_trigger(record)
            record["stop"] = self.protective_stop_price(record)
            self.watchlist[record["ticker"]] = record
            self.pending_candidates.append(record)
            self.candidate_rankings.append(record)

        rejected = setup_df.filter(~pl.col("passes_setup_filter")).select(
            "ticker",
            "reject_reason",
            "orb_relative_volume",
            "box_close",
            "avg_daily_volume_14",
            "atr_14",
            "gap_pct",
            "range_atr",
            "close_location",
            "body_to_range",
        )
        for row in rejected.iter_rows(named=True):
            self.rejection_events.append(
                {
                    "session_date": frames.session_date.isoformat(),
                    "timestamp": self.opening_range_available_timestamp(frames.session_date),
                    **row,
                }
            )

        return self._session_frame(frames.event_frame)

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        minute = context.timestamp.hour * 60 + context.timestamp.minute
        requests: list[OrderRequest] = []

        requests.extend(self._cancel_unfilled_entries(context, pending_orders))
        requests.extend(self._position_exit_requests(context, portfolio))

        exiting_symbols = {request.symbol for request in requests if request.side == "SELL"}
        if exiting_symbols:
            return requests
        self._clear_active_if_done(portfolio, pending_orders, exiting_symbols)

        if minute < self.first_entry_action_minute():
            return requests
        if minute >= self.config.entry_cutoff_minute:
            return requests
        if self._has_active_trade(portfolio, pending_orders, exiting_symbols):
            return requests

        request = self._next_entry_request(context, portfolio, pending_orders, exiting_symbols)
        if request is not None:
            requests.append(request)
        return requests

    def on_day_end(self, timestamp: datetime, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        for symbol, position in list(portfolio.positions.items()):
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="MARKET",
                    reason="EOD",
                    tag=f"EXIT|reason=EOD|held_symbol={symbol}",
                )
            )
        return requests

    def artifacts(self) -> dict[str, list[dict]]:
        return {
            "scanner_snapshots": self.scanner_snapshots,
            "candidate_rankings": self.candidate_rankings,
            "live_rankings": self.live_rankings,
            "signal_events": self.signal_events,
            "rejection_events": self.rejection_events,
        }

    def entry_metadata(self, order: Order) -> dict:
        return self.entry_order_metadata.get(order.symbol, {})

    def _build_setup_dataframe(self, frames: DayFrames) -> pl.DataFrame:
        cfg = self.config
        box = (
            self._session_frame(frames.event_frame)
            .filter(
                (pl.col("minute_of_day") >= cfg.opening_box_start_minute)
                & (pl.col("minute_of_day") <= cfg.opening_box_end_minute)
            )
            .group_by("ticker")
            .agg(
                pl.col("open").first().alias("box_open"),
                pl.col("high").max().alias("box_high"),
                pl.col("low").min().alias("box_low"),
                pl.col("close").last().alias("box_close"),
                pl.col("volume").sum().alias("box_volume"),
                pl.col("transactions").sum().alias("box_transactions"),
                pl.len().alias("box_bar_count"),
            )
            .with_columns(
                (pl.col("box_high") - pl.col("box_low")).alias("box_range"),
                ((pl.col("box_high") + pl.col("box_low")) / 2.0).alias("box_mid"),
            )
            .join(frames.daily_context, on="ticker", how="left")
            .with_columns(
                (pl.col("avg_daily_volume_14") * pl.col("previous_close")).alias("avg_daily_dollar_volume_14"),
                pl.when(pl.col("avg_daily_volume_14") > 0)
                .then(pl.col("box_volume") / (pl.col("avg_daily_volume_14") * cfg.relative_volume_daily_share))
                .otherwise(0.0)
                .alias("orb_relative_volume"),
                pl.when(pl.col("previous_close") > 0)
                .then((pl.col("box_open") / pl.col("previous_close")) - 1.0)
                .otherwise(0.0)
                .alias("gap_pct"),
                pl.when(pl.col("atr_14") > 0)
                .then(pl.col("box_range") / pl.col("atr_14"))
                .otherwise(0.0)
                .alias("range_atr"),
                pl.when(pl.col("box_range") > 0)
                .then((pl.col("box_close") - pl.col("box_low")) / pl.col("box_range"))
                .otherwise(0.0)
                .alias("close_location"),
                pl.when(pl.col("box_range") > 0)
                .then((pl.col("box_close") - pl.col("box_open")).abs() / pl.col("box_range"))
                .otherwise(0.0)
                .alias("body_to_range"),
            )
        )

        universe = self._apply_universe_selection(box)
        return universe.with_columns(self._setup_pass_expr().alias("passes_setup_filter")).with_columns(
            self._reject_reason_expr().alias("reject_reason")
        )

    def _apply_universe_selection(self, frame: pl.DataFrame) -> pl.DataFrame:
        cfg = self.config
        suffix_filter = pl.lit(True)
        for suffix in BLOCKED_SUFFIXES:
            suffix_filter = suffix_filter & ~pl.col("ticker").str.ends_with(suffix)
        return (
            frame.filter(
                ~pl.col("ticker").is_in(BLOCKED_SYMBOLS)
                & suffix_filter
                & (pl.col("previous_close") >= cfg.min_universe_price)
                & (pl.col("previous_close") <= cfg.max_price)
                & (pl.col("avg_daily_dollar_volume_14") >= cfg.min_daily_dollar_volume)
            )
            .sort("avg_daily_dollar_volume_14", descending=True)
            .head(cfg.max_universe_size)
        )

    def _setup_pass_expr(self) -> pl.Expr:
        cfg = self.config
        return (
            (pl.col("box_open").is_not_null())
            & (pl.col("box_close") >= cfg.min_price)
            & (pl.col("avg_daily_volume_14").is_not_null())
            & (pl.col("avg_daily_volume_14") >= cfg.min_avg_daily_volume)
            & (pl.col("atr_14").is_not_null())
            & (pl.col("atr_14") >= cfg.min_atr)
            & (pl.col("orb_relative_volume") >= cfg.min_opening_relative_volume)
            & (pl.col("gap_pct") >= cfg.min_gap_up_pct)
            & (pl.col("box_close") > pl.col("box_open"))
            & (pl.col("box_high") > pl.col("box_low"))
            & (pl.col("range_atr") >= cfg.min_orb_range_atr_fraction)
            & (pl.col("range_atr") <= cfg.max_orb_range_atr_fraction)
            & (pl.col("close_location") >= cfg.min_close_location)
            & (pl.col("body_to_range") >= cfg.min_body_to_range)
        ).fill_null(False)

    def _reject_reason_expr(self) -> pl.Expr:
        cfg = self.config
        return (
            pl.when(pl.col("box_open").is_null()).then(pl.lit("base"))
            .when(pl.col("box_close") < cfg.min_price).then(pl.lit("price_low"))
            .when(pl.col("avg_daily_volume_14").is_null()).then(pl.lit("liquidity"))
            .when(pl.col("avg_daily_volume_14") < cfg.min_avg_daily_volume).then(pl.lit("liquidity"))
            .when(pl.col("atr_14").is_null()).then(pl.lit("atr"))
            .when(pl.col("atr_14") < cfg.min_atr).then(pl.lit("atr"))
            .when(pl.col("orb_relative_volume") < cfg.min_opening_relative_volume).then(pl.lit("relative_volume"))
            .when(pl.col("gap_pct") < cfg.min_gap_up_pct).then(pl.lit("gap"))
            .when(pl.col("box_close") <= pl.col("box_open")).then(pl.lit("shape"))
            .when(pl.col("box_high") <= pl.col("box_low")).then(pl.lit("range"))
            .when(pl.col("range_atr") < cfg.min_orb_range_atr_fraction).then(pl.lit("range_small"))
            .when(pl.col("range_atr") > cfg.max_orb_range_atr_fraction).then(pl.lit("range_large"))
            .when(pl.col("close_location") < cfg.min_close_location).then(pl.lit("close_location"))
            .when(pl.col("body_to_range") < cfg.min_body_to_range).then(pl.lit("body"))
            .otherwise(pl.lit("passed"))
        )

    def _next_entry_request(
        self,
        context: BarContext,
        portfolio: Portfolio,
        pending_orders: list[Order],
        exiting_symbols: set[str],
    ) -> OrderRequest | None:
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        while self.pending_candidates:
            candidate = self.pending_candidates.pop(0)
            symbol = candidate["ticker"]
            if symbol in portfolio.positions or symbol in pending_symbols or symbol in exiting_symbols:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is not None and self._is_red_bar(bar):
                self._reject(context.timestamp, symbol, "red_entry_bar", candidate)
                continue
            if bar is not None and not self._macd_entry_filter_passes(bar):
                self._reject(
                    context.timestamp,
                    symbol,
                    "macd_below_signal",
                    {
                        **candidate,
                        "macd_line": bar.get("macd_line"),
                        "macd_signal": bar.get("macd_signal"),
                    },
                )
                continue
            request = self._entry_request(candidate, context, portfolio)
            if request is not None:
                return request
        return None

    def _entry_request(self, candidate: dict, context: BarContext, portfolio: Portfolio) -> OrderRequest | None:
        entry = self.entry_trigger(candidate)
        stop = self.protective_stop_price(candidate)
        quantity = self.calculate_quantity(entry, stop, portfolio)
        if quantity <= 0:
            self._reject(context.timestamp, candidate["ticker"], "quantity", candidate)
            return None
        if not self.has_minimum_trade_economics(quantity, entry, stop):
            self._reject(context.timestamp, candidate["ticker"], "economics", candidate)
            return None

        self.active_symbol = candidate["ticker"]
        self.entry_order_metadata[candidate["ticker"]] = {
            "setup_rank": candidate["rank"],
            "live_rank": candidate["rank"],
            "setup_score": candidate["orb_relative_volume"],
            "live_score": candidate["orb_relative_volume"],
            "stop_price": stop,
        }
        self.signal_events.append(
            {
                "timestamp": context.timestamp,
                "ticker": candidate["ticker"],
                "event": "ORB_GREEN_STOP_SUBMITTED",
                "rank": candidate["rank"],
                "entry": entry,
                "trigger": entry,
                "stop": stop,
                "quantity": quantity,
                "orb_relative_volume": candidate["orb_relative_volume"],
                "atr_14": candidate["atr_14"],
            }
        )
        self._trace_entry(context.timestamp, candidate, quantity, entry, stop)
        return OrderRequest(
            symbol=candidate["ticker"],
            side="BUY",
            quantity=quantity,
            order_type="STOP",
            stop_price=entry,
            reason="ORB_GREEN_STOP_ENTRY",
            tag=(
                f"ENTRY|type=STOP|rule=GREEN_ORB_STOP|rank={candidate['rank']}|qty={quantity}"
                f"|trigger={entry:.2f}|stop={stop:.2f}|rv={candidate['orb_relative_volume']:.1f}"
                f"|atr={candidate['atr_14']:.2f}|or={candidate['box_low']:.2f}-{candidate['box_high']:.2f}"
            ),
            fill_requires_green_bar=True,
        )

    def _cancel_unfilled_entries(self, context: BarContext, pending_orders: list[Order]) -> list[OrderRequest]:
        minute = context.timestamp.hour * 60 + context.timestamp.minute
        if minute < 16 * 60 - self.config.cancel_unfilled_minutes_before_close:
            return []
        requests = []
        for order in pending_orders:
            if order.side != "BUY" or order.status != "OPEN":
                continue
            requests.append(
                OrderRequest(
                    symbol=order.symbol,
                    side="BUY",
                    quantity=0,
                    order_type="CANCEL",
                    reason="ORB_CANCEL_EOD",
                    tag="CANCEL_ENTRY|reason=orb_cancel_eod",
                )
            )
            self._reject(context.timestamp, order.symbol, "orb_cancel_eod", self.watchlist.get(order.symbol, {}))
        return requests

    def _position_exit_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        minute = context.timestamp.hour * 60 + context.timestamp.minute
        for symbol, position in list(portfolio.positions.items()):
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            take_profit_price = position.entry_price * (1.0 + self.config.take_profit_pocket_pct)
            if (
                self.config.take_profit_pocket_pct > 0
                and context.timestamp > position.entry_time
                and float(bar["close"]) >= take_profit_price
            ):
                self._trace_exit(context.timestamp, symbol, "POCKETING", position, bar)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="POCKETING",
                        tag=self._exit_tag("POCKETING", position, bar),
                    )
                )
                continue
            if float(bar["low"]) <= position.stop_price:
                self._trace_exit(context.timestamp, symbol, "STOP_LOSS", position, bar)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="STOP",
                        stop_price=position.stop_price,
                        reason="STOP_LOSS",
                        tag=self._exit_tag("STOP_LOSS", position, bar),
                        allow_same_bar_fill=True,
                    )
                )
                continue
            if minute >= 16 * 60 - self.config.exit_minutes_before_close:
                self._trace_exit(context.timestamp, symbol, "EOD", position, bar)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="EOD",
                        tag=self._exit_tag("EOD", position, bar),
                    )
                )
        return requests

    def entry_trigger(self, setup: dict) -> float:
        return float(setup["box_high"]) * (1.0 + self.config.entry_buffer_pct)

    def protective_stop_price(self, setup: dict) -> float:
        return self.entry_trigger(setup) - (float(setup["atr_14"]) * self.config.atr_stop_fraction)

    def calculate_quantity(self, entry: float, stop: float, portfolio: Portfolio) -> int:
        if entry <= 0 or abs(entry - stop) <= 0:
            return 0
        deployable_cash = max(0.0, portfolio.cash - (portfolio.total_equity() * self.config.cash_reserve_pct))
        return max(0, min(int(deployable_cash / entry), int(portfolio.cash / entry)))

    def has_minimum_trade_economics(self, quantity: int, entry: float, stop: float) -> bool:
        risk_per_share = abs(entry - stop)
        position_value = quantity * entry
        planned_risk = quantity * risk_per_share
        return position_value >= self.config.min_position_value and planned_risk >= self.config.min_planned_risk_dollars

    def _has_active_trade(self, portfolio: Portfolio, pending_orders: list[Order], exiting_symbols: set[str]) -> bool:
        if self.active_symbol is None:
            return False
        if self.active_symbol in exiting_symbols:
            return False
        if self.active_symbol in portfolio.positions:
            return True
        return any(order.symbol == self.active_symbol and order.status == "OPEN" for order in pending_orders)

    def _clear_active_if_done(self, portfolio: Portfolio, pending_orders: list[Order], exiting_symbols: set[str]) -> None:
        if self.active_symbol is None:
            return
        if self._has_active_trade(portfolio, pending_orders, exiting_symbols):
            return
        self.active_symbol = None

    def _session_frame(self, frame: pl.DataFrame) -> pl.DataFrame:
        return frame.filter(
            (pl.col("minute_of_day") >= self.config.opening_box_start_minute)
            & (pl.col("minute_of_day") < 16 * 60)
        )

    def first_entry_action_minute(self) -> int:
        return self.config.opening_box_end_minute + 1

    def opening_range_available_timestamp(self, session_date) -> str:
        minute = self.first_entry_action_minute()
        return f"{session_date.isoformat()} {minute // 60:02d}:{minute % 60:02d}:00"

    def _reject(self, timestamp: datetime, ticker: str, reason: str, setup: dict) -> None:
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": ticker,
                "reject_reason": reason,
                "setup_rank": setup.get("rank"),
                "orb_relative_volume": setup.get("orb_relative_volume"),
                "trigger": setup.get("trigger"),
                "stop": setup.get("stop"),
                "macd_line": setup.get("macd_line"),
                "macd_signal": setup.get("macd_signal"),
            }
        )
        if self.observability:
            self.observability.trace(
                timestamp=timestamp,
                ticker=ticker,
                stage="risk_check",
                event_type="entry_rejected",
                decision="skip",
                reason_code=reason,
                reason=f"ORB candidate skipped by {reason}",
                values={
                    "rank": setup.get("rank"),
                    "orb_relative_volume": setup.get("orb_relative_volume"),
                    "trigger": setup.get("trigger"),
                    "stop": setup.get("stop"),
                    "macd_line": setup.get("macd_line"),
                    "macd_signal": setup.get("macd_signal"),
                },
            )

    def _observe_setup_scan(
        self,
        frames: DayFrames,
        setup_df: pl.DataFrame,
        candidates: pl.DataFrame,
        selected: pl.DataFrame,
        portfolio: Portfolio,
    ) -> None:
        if not self.observability:
            return
        scan_time = self.opening_range_available_timestamp(frames.session_date)
        rows = (
            setup_df.with_columns(
                pl.when(pl.col("passes_setup_filter")).then(pl.lit("candidate")).otherwise(pl.lit("filtered_out")).alias("scanner_status"),
                pl.col("reject_reason").alias("reason_code"),
                pl.col("orb_relative_volume").alias("setup_score"),
            )
            .to_dicts()
        )
        self.observability.scanner(timestamp=scan_time, rows=rows, score_key="setup_score", stage="qc_orb_setup_scanner")
        self.observability.trace(
            timestamp=scan_time,
            stage="qc_orb_setup_scanner",
            event_type="setup_scan_complete",
            decision="rank_candidates",
            reason_code="opening_range_complete",
            reason="QuantConnect ORB opening range scan completed",
            values={
                "total_scanned": setup_df.height,
                "candidate_count": candidates.height,
                "selected_count": selected.height,
                "max_candidates": self.config.max_candidates,
            },
            state={"watchlist": [row["ticker"] for row in selected.select("ticker").to_dicts()]},
        )
        self.observability.state(
            timestamp=scan_time,
            scope="strategy",
            state={
                "candidate_count": candidates.height,
                "selected_count": selected.height,
                "open_positions": len(portfolio.positions),
            },
        )

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, entry: float, stop: float) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=candidate["ticker"],
            stage="order_request",
            event_type="entry_intent",
            decision="submit_order",
            reason_code="ORB_GREEN_STOP_ENTRY",
            reason="Ranked ORB candidate submitted as a buy stop that can only fill on a non-red triggering candle",
            values={
                "rank": candidate.get("rank"),
                "quantity": quantity,
                "entry": entry,
                "stop": stop,
                "orb_relative_volume": candidate.get("orb_relative_volume"),
                "atr_14": candidate.get("atr_14"),
                "box_high": candidate.get("box_high"),
                "box_low": candidate.get("box_low"),
            },
            force=self._force_trade_trace(),
        )

    def _trace_exit(self, timestamp: datetime, symbol: str, reason: str, position, bar: dict) -> None:
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
                "price": float(bar["close"]),
                "entry_price": position.entry_price,
                "stop": position.stop_price,
                "max_unrealized_profit": position.max_unrealized_profit,
            },
            force=self._force_trade_trace(),
        )

    def _exit_tag(self, reason: str, position, bar: dict) -> str:
        return (
            f"EXIT|reason={reason}|price={float(bar['close']):.2f}|stop={position.stop_price:.2f}"
            f"|maxp={position.max_price:.2f}|maxu={position.max_unrealized_profit:.2f}|maxR={position.max_r_multiple:.2f}"
        )

    def _force_trade_trace(self) -> bool:
        return bool(self.observability and self.observability.config.observability_always_trace_trades)

    def _is_red_bar(self, bar: dict) -> bool:
        try:
            return float(bar.get("close")) < float(bar.get("open"))
        except (TypeError, ValueError):
            return False

    def _macd_entry_filter_passes(self, bar: dict) -> bool:
        try:
            return float(bar.get("macd_line")) >= float(bar.get("macd_signal"))
        except (TypeError, ValueError):
            return True

    def _float_or_none(self, value) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if numeric != numeric:
            return None
        return numeric
