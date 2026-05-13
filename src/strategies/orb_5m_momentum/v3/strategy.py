from __future__ import annotations

from datetime import datetime

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.portfolio import Portfolio
from src.strategies.orb_5m_momentum.v2.strategy import OrbFiveMinuteMomentumV2Strategy
from src.strategies.orb_5m_momentum.v3.config import OrbMomentumConfig
from src.strategies.orb_5m_momentum.v3.presentation import chart_presentation


class OrbFiveMinuteMomentumV3Strategy(OrbFiveMinuteMomentumV2Strategy):
    name = "orb_5m_momentum"

    def __init__(self, config: OrbMomentumConfig | None = None):
        super().__init__(config or OrbMomentumConfig())

    def data_requirements(self) -> DataRequirements:
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=("core", "session"),
            required_columns=("ticker", "bar_time_market", "minute_of_day", "open", "high", "low", "close", "volume", "transactions"),
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def _build_setup_dataframe(self, frames: DayFrames) -> pl.DataFrame:
        cfg = self.config
        box = (
            self._session_frame(frames.event_frame).filter(
                (pl.col("minute_of_day") >= cfg.opening_box_start_minute)
                & (pl.col("minute_of_day") < cfg.opening_box_end_minute)
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
            .with_columns(
                (pl.col("box_volume") * pl.col("box_close")).alias("box_dollar_volume"),
                pl.when(pl.col("box_low") > 0)
                .then(pl.col("box_range") / pl.col("box_low"))
                .otherwise(0.0)
                .alias("box_strength"),
                pl.when(pl.col("box_open") > 0)
                .then(pl.col("box_range") / pl.col("box_open"))
                .otherwise(0.0)
                .alias("box_range_pct"),
            )
            .with_columns((pl.col("box_strength") * 10_000.0).alias("setup_score"))
            .with_columns(self._setup_pass_expr().alias("passes_setup_filter"))
            .with_columns(self._reject_reason_expr().alias("reject_reason"))
        )
        return box

    def _setup_pass_expr(self) -> pl.Expr:
        cfg = self.config
        return (
            (pl.col("box_close") >= cfg.min_price)
            & (pl.col("box_close") <= cfg.max_price)
            & (pl.col("box_volume") >= cfg.min_opening_volume)
            & (pl.col("box_dollar_volume") >= cfg.min_opening_dollar_volume)
            & (pl.col("box_low") > 0)
            & (pl.col("box_range") > 0)
        ).fill_null(False)

    def _reject_reason_expr(self) -> pl.Expr:
        cfg = self.config
        return (
            pl.when(pl.col("box_close") < cfg.min_price).then(pl.lit("price_low"))
            .when(pl.col("box_close") > cfg.max_price).then(pl.lit("price_high"))
            .when(pl.col("box_volume") < cfg.min_opening_volume).then(pl.lit("opening_volume"))
            .when(pl.col("box_dollar_volume") < cfg.min_opening_dollar_volume).then(pl.lit("opening_liquidity"))
            .when(pl.col("box_low") <= 0).then(pl.lit("bad_box_low"))
            .when(pl.col("box_range") <= 0).then(pl.lit("empty_box_range"))
            .otherwise(pl.lit("passed"))
        )

    def _live_candidates(
        self,
        context: BarContext,
        portfolio: Portfolio,
        pending_orders: list[Order],
    ) -> list[dict]:
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        candidates = []
        live_rows = []
        for ticker, setup in self.watchlist.items():
            bar = context.updates_by_symbol.get(ticker)
            if bar is None:
                continue

            trigger = self.entry_trigger(setup)
            stop = self.protective_stop_price(setup)
            last_price = float(bar["close"])
            live_score = self.live_score(setup, bar)
            status = "eligible"
            reason = ""
            breakout_extension_pct = (last_price / trigger) - 1.0 if trigger > 0 else 0.0

            if ticker in portfolio.positions:
                status = "held"
                reason = "already_held"
            elif ticker in pending_symbols:
                status = "pending"
                reason = "entry_pending"
            elif not self.breakout_armed.get(ticker, True):
                status = "inactive"
                reason = "not_armed"
            elif last_price <= trigger:
                status = "inactive"
                reason = "waiting_for_range_break"
            elif breakout_extension_pct > self.config.max_entry_extension_pct:
                status = "invalid"
                reason = "extended_breakout_close"

            live_rows.append(
                {
                    "session_date": self.session_date.isoformat() if self.session_date else "",
                    "timestamp": context.timestamp,
                    "ticker": ticker,
                    "setup_rank": setup.get("rank"),
                    "setup_score": setup.get("setup_score"),
                    "live_score": live_score,
                    "status": status,
                    "reason": reason,
                    "price": last_price,
                    "trigger": trigger,
                    "stop": stop,
                    "box_high": setup.get("box_high"),
                    "box_mid": setup.get("box_mid"),
                    "box_low": setup.get("box_low"),
                    "box_volume": setup.get("box_volume"),
                    "box_dollar_volume": setup.get("box_dollar_volume"),
                    "box_strength": setup.get("box_strength"),
                    "box_range_pct": setup.get("box_range_pct"),
                    "breakout_extension_pct": breakout_extension_pct,
                }
            )

            if status != "eligible":
                if status == "invalid":
                    self._reject(context.timestamp, ticker, reason, setup, bar, live_score)
                continue

            candidates.append(
                {
                    **setup,
                    "timestamp": context.timestamp,
                    "last_price": last_price,
                    "live_score": live_score,
                    "trigger": trigger,
                    "stop": stop,
                }
            )

        live_rows.sort(key=lambda item: float(item.get("live_score") or 0.0), reverse=True)
        for live_rank, row in enumerate(live_rows, start=1):
            row["live_rank"] = live_rank
            self.live_rankings.append(row)
        if self.observability and live_rows:
            self.observability.scanner(
                timestamp=context.timestamp,
                rows=live_rows,
                score_key="live_score",
                stage="live_scanner",
            )
            self.observability.state(
                timestamp=context.timestamp,
                scope="strategy",
                state=self._portfolio_state(portfolio, pending_orders)
                | {
                    "watchlist_count": len(self.watchlist),
                    "live_rows": len(live_rows),
                    "eligible_count": len([row for row in live_rows if row.get("status") == "eligible"]),
                },
            )

        candidates.sort(key=lambda item: item["live_score"], reverse=True)
        for live_rank, candidate in enumerate(candidates[:10], start=1):
            self.signal_events.append(
                {
                    "timestamp": context.timestamp,
                    "ticker": candidate["ticker"],
                    "event": "LIVE_CANDIDATE",
                    "live_rank": live_rank,
                    "setup_rank": candidate["rank"],
                    "setup_score": candidate["setup_score"],
                    "live_score": candidate["live_score"],
                    "price": candidate["last_price"],
                    "trigger": candidate["trigger"],
                    "stop": candidate["stop"],
                }
            )
        return candidates

    def _cancel_invalid_entries(self, context: BarContext, pending_orders: list[Order]) -> list[OrderRequest]:
        requests = []
        minute = context.timestamp.hour * 60 + context.timestamp.minute
        for order in pending_orders:
            if order.side != "BUY" or order.status != "OPEN":
                continue
            setup = self.watchlist.get(order.symbol)
            bar = context.updates_by_symbol.get(order.symbol)
            if setup is None or bar is None:
                continue
            reason = None
            last_price = float(bar["close"])
            trigger = self.entry_trigger(setup)
            if minute >= self.config.entry_cutoff_minute:
                reason = "entry_cutoff"
            elif trigger > 0 and (last_price / trigger) - 1.0 > self.config.max_entry_extension_pct:
                reason = "missed_breakout"
            if reason is None:
                continue
            self.breakout_armed[order.symbol] = True
            self._trace(
                timestamp=context.timestamp,
                ticker=order.symbol,
                stage="entry_order_management",
                event_type="entry_order_cancel_requested",
                decision="cancel_order",
                reason_code=reason,
                reason=f"Pending entry no longer satisfies {reason}",
                values={
                    "last_price": last_price,
                    "trigger": trigger,
                    "stop": self.protective_stop_price(setup),
                    "order_id": order.order_id,
                },
                force=self._force_trade_trace(),
            )
            requests.append(
                OrderRequest(
                    symbol=order.symbol,
                    side="BUY",
                    quantity=0,
                    order_type="CANCEL",
                    reason=reason,
                    tag=f"CANCEL_ENTRY|reason={reason}|trigger={trigger:.2f}",
                )
            )
        return requests

    def _entry_request(
        self,
        candidate: dict,
        live_rank: int,
        top_score: float,
        portfolio: Portfolio,
        live_candidate_count: int,
    ) -> OrderRequest | None:
        score_quality = max(0.0, min(candidate["live_score"] / top_score, 1.0))
        risk_pct = self.risk_pct_for_score(score_quality)
        entry_price = float(candidate["last_price"])
        quantity = self.calculate_quantity(
            entry_price,
            candidate["stop"],
            score_quality,
            risk_pct,
            live_candidate_count,
            portfolio,
        )
        if quantity <= 0:
            self.rejection_events.append(
                {
                    "timestamp": candidate["timestamp"],
                    "ticker": candidate["ticker"],
                    "reject_reason": "quantity",
                    "live_score": candidate["live_score"],
                    "trigger": candidate["trigger"],
                    "stop": candidate["stop"],
                }
            )
            self._trace(
                timestamp=candidate["timestamp"],
                ticker=candidate["ticker"],
                stage="risk_check",
                event_type="entry_rejected",
                decision="skip",
                reason_code="quantity",
                reason="Calculated quantity was zero",
                values={
                    "live_score": candidate["live_score"],
                    "trigger": candidate["trigger"],
                    "stop": candidate["stop"],
                    "entry": entry_price,
                    "risk_pct": risk_pct,
                },
                state=self._portfolio_state(portfolio, []),
                force=self._force_trade_trace(),
            )
            return None

        metadata = {
            "setup_rank": candidate["rank"],
            "live_rank": live_rank,
            "setup_score": candidate["setup_score"],
            "live_score": candidate["live_score"],
            "stop_price": candidate["stop"],
        }
        self.entry_order_metadata[candidate["ticker"]] = metadata
        self.breakout_armed[candidate["ticker"]] = False
        self._trace(
            timestamp=candidate["timestamp"],
            ticker=candidate["ticker"],
            stage="order_request",
            event_type="entry_intent",
            decision="submit_order",
            reason_code="LIVE_SIGNAL",
            reason="Range breakout passed price microstructure, risk, and portfolio checks",
            values={
                "quantity": quantity,
                "entry_price": entry_price,
                "trigger": candidate["trigger"],
                "stop": candidate["stop"],
                "setup_rank": candidate["rank"],
                "live_rank": live_rank,
                "setup_score": candidate["setup_score"],
                "live_score": candidate["live_score"],
                "box_strength": candidate.get("box_strength"),
                "risk_pct": risk_pct,
            },
            state=self._portfolio_state(portfolio, []),
            force=self._force_trade_trace(),
        )
        tag = (
            f"ENTRY|type=MARKET|rule=1M_CLOSE_ORB_STRENGTH|rank={candidate['rank']}|lrank={live_rank}"
            f"|qty={quantity}|entry={entry_price:.2f}|trigger={candidate['trigger']:.2f}|stop={candidate['stop']:.2f}"
            f"|box_high={candidate['box_high']:.2f}|box_mid={candidate['box_mid']:.2f}|box_low={candidate['box_low']:.2f}"
            f"|strength={float(candidate.get('box_strength') or 0.0):.4f}|setup={candidate['setup_score']:.1f}"
            f"|live={candidate['live_score']:.1f}|boxvol={float(candidate['box_volume']):.0f}|rp={risk_pct * 100:.2f}"
        )
        return OrderRequest(
            symbol=candidate["ticker"],
            side="BUY",
            quantity=quantity,
            order_type="MARKET",
            reason="LIVE_SIGNAL",
            tag=tag,
        )

    def _position_exit_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        for symbol, position in list(portfolio.positions.items()):
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue

            close = float(bar["close"])
            if close <= position.stop_price:
                self._trace_exit_intent(context.timestamp, symbol, "BREAKOUT_FAIL", position, bar, portfolio)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="BREAKOUT_FAIL",
                        tag=self._exit_tag("BREAKOUT_FAIL", position, bar),
                    )
                )
                continue

            held_minutes = (context.timestamp - position.entry_time).total_seconds() / 60.0
            if held_minutes >= self.config.minimum_hold_minutes and position.max_r_multiple >= self.config.trailing_activation_r:
                trail_price = self.trailing_stop_price(position)
                if close <= trail_price:
                    self._trace_exit_intent(context.timestamp, symbol, "GIVEBACK", position, bar, portfolio)
                    requests.append(
                        OrderRequest(
                            symbol=symbol,
                            side="SELL",
                            quantity=position.quantity,
                            order_type="MARKET",
                            reason="GIVEBACK",
                            tag=self._exit_tag("GIVEBACK", position, bar),
                        )
                    )
        return requests

    def live_score(self, setup: dict, bar: dict) -> float:
        last_price = float(bar["close"])
        trigger = self.entry_trigger(setup)
        extension = max(0.0, (last_price / trigger) - 1.0) if trigger > 0 else 0.0
        extension_score = min(extension / max(self.config.max_entry_extension_pct, 0.000001), 1.0) * 20.0
        return float(setup["setup_score"]) + extension_score

    def protective_stop_price(self, setup: dict) -> float:
        return float(setup["box_mid"])

    def trailing_stop_price(self, position) -> float:
        open_profit_per_share = max(0.0, position.max_price - position.entry_price)
        giveback = open_profit_per_share * self.config.trailing_giveback_fraction
        return max(position.stop_price, position.max_price - giveback)

    def _exit_tag(self, reason: str, position, bar: dict) -> str:
        trail = self.trailing_stop_price(position)
        return (
            f"EXIT|reason={reason}|price={float(bar['close']):.2f}|stop={position.stop_price:.2f}"
            f"|trail={trail:.2f}|maxp={position.max_price:.2f}|maxu={position.max_unrealized_profit:.2f}"
            f"|maxR={position.max_r_multiple:.2f}"
        )

    def _trace_exit_intent(self, timestamp: datetime, symbol: str, reason: str, position, bar: dict, portfolio: Portfolio) -> None:
        self._trace(
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
                "trail": self.trailing_stop_price(position),
                "max_price": position.max_price,
                "max_unrealized_profit": position.max_unrealized_profit,
                "max_r_multiple": position.max_r_multiple,
            },
            state=self._portfolio_state(portfolio, []),
            force=self._force_trade_trace(),
        )
