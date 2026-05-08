from __future__ import annotations

from datetime import datetime

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import MinuteContext, Order, OrderRequest
from src.backtest.portfolio import Portfolio
from src.strategies.orb_5m_momentum.config import OrbMomentumConfig


class OrbFiveMinuteMomentumStrategy:
    name = "orb_5m_momentum"

    def __init__(self, config: OrbMomentumConfig | None = None):
        self.config = config or OrbMomentumConfig()
        self.session_date = None
        self.watchlist: dict[str, dict] = {}
        self.entry_order_metadata: dict[str, dict] = {}
        self.breakout_armed: dict[str, bool] = {}
        self.scanner_snapshots: list[dict] = []
        self.candidate_rankings: list[dict] = []
        self.live_rankings: list[dict] = []
        self.signal_events: list[dict] = []
        self.rejection_events: list[dict] = []

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> None:
        self.session_date = frames.session_date
        self.watchlist = {}
        self.entry_order_metadata = {}
        self.breakout_armed = {}

        setup_df = self._build_setup_dataframe(frames)
        candidates = setup_df.filter(pl.col("passes_setup_filter")).sort("setup_score", descending=True)
        selected = candidates.head(self.config.watchlist_size)

        self.scanner_snapshots.append(
            {
                "session_date": frames.session_date.isoformat(),
                "timestamp": f"{frames.session_date.isoformat()} 09:35:00",
                "candidate_count": candidates.height,
                "selected_count": selected.height,
                "watchlist_size": self.config.watchlist_size,
            }
        )

        for rank, row in enumerate(selected.iter_rows(named=True), start=1):
            record = dict(row)
            record["session_date"] = frames.session_date.isoformat()
            record["rank"] = rank
            self.watchlist[row["ticker"]] = record
            self.breakout_armed[row["ticker"]] = True
            self.candidate_rankings.append(record)

        rejected = setup_df.filter(~pl.col("passes_setup_filter")).select(
            "ticker", "reject_reason", "setup_score", "box_close", "avg_daily_volume_14", "atr_14"
        )
        for row in rejected.iter_rows(named=True):
            self.rejection_events.append(
                {
                    "session_date": frames.session_date.isoformat(),
                    "timestamp": f"{frames.session_date.isoformat()} 09:35:00",
                    **row,
                }
            )

    def on_minute(self, context: MinuteContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        minute = context.timestamp.hour * 60 + context.timestamp.minute

        requests.extend(self._cancel_invalid_entries(context, pending_orders))
        requests.extend(self._position_exit_requests(context, portfolio))

        if minute < self.config.opening_box_end_minute:
            return requests

        if minute >= self.config.entry_cutoff_minute:
            return requests

        live_candidates = self._live_candidates(context, portfolio, pending_orders)
        if not live_candidates:
            return requests

        open_slots = self.config.max_active_positions - self._occupied_slot_count(portfolio, pending_orders)
        if open_slots > 0:
            top_score = max(live_candidates[0]["live_score"], 1.0)
            for live_rank, candidate in enumerate(live_candidates, start=1):
                if self._occupied_slot_count(portfolio, pending_orders) + len(requests) >= self.config.max_active_positions:
                    break
                if candidate["ticker"] in portfolio.positions:
                    continue
                request = self._entry_request(candidate, live_rank, top_score, portfolio, len(live_candidates))
                if request is not None:
                    requests.append(request)
            return requests

        replacement = self._replacement_candidate(live_candidates, portfolio, context.timestamp)
        if replacement is not None:
            new_candidate, old_symbol = replacement
            position = portfolio.positions[old_symbol]
            requests.append(
                OrderRequest(
                    symbol=old_symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="MARKET",
                    reason="ROTATE_OUT",
                    tag=f"EXIT|reason=ROTATE_OUT|rotate_to={new_candidate['ticker']}|new_score={new_candidate['live_score']:.1f}",
                )
            )
        return requests

    def _cancel_invalid_entries(self, context: MinuteContext, pending_orders: list[Order]) -> list[OrderRequest]:
        requests = []
        minute = context.timestamp.hour * 60 + context.timestamp.minute
        for order in pending_orders:
            if order.side != "BUY" or order.status != "OPEN":
                continue
            setup = self.watchlist.get(order.symbol)
            bar = context.bars_by_symbol.get(order.symbol)
            if setup is None or bar is None:
                continue
            reason = None
            last_price = float(bar["close"])
            if minute >= self.config.entry_cutoff_minute:
                reason = "entry_cutoff"
            elif last_price < self.protective_stop_price(setup):
                reason = "lost_breakout_zone"
            elif last_price > self.entry_trigger(setup) * (1.0 + self.config.entry_stage_proximity_pct):
                reason = "missed_breakout"
            elif not self._macd_open(bar):
                reason = "macd_closed"
            elif not self._tema_open(bar, setup):
                reason = "tema_closed"
            if reason is None:
                continue
            self.breakout_armed[order.symbol] = True
            requests.append(
                OrderRequest(
                    symbol=order.symbol,
                    side="BUY",
                    quantity=0,
                    order_type="CANCEL",
                    reason=reason,
                    tag=f"CANCEL_ENTRY|reason={reason}|trigger={self.entry_trigger(setup):.2f}",
                )
            )
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
            frames.minute_bars.filter(
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
            .join(frames.prior_daily_stats, on="ticker", how="left")
            .with_columns(
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

        return (
            box.with_columns(
                (1.0 - ((pl.col("range_atr") - 0.30).abs() / 0.30)).clip(0.0, 1.0).alias("ideal_range_score"),
                (pl.col("orb_relative_volume").clip(0.0, 10.0) / 10.0).alias("rv_score"),
                (pl.col("gap_pct").clip(0.0, 0.10) / 0.10).alias("gap_score"),
                (pl.col("avg_daily_volume_14").fill_null(0.0) / 10_000_000.0).clip(0.0, 1.0).alias(
                    "liquidity_score"
                ),
            )
            .with_columns(
                (
                    35.0 * pl.col("rv_score")
                    + 20.0 * pl.col("close_location")
                    + 15.0 * pl.col("gap_score")
                    + 15.0 * pl.col("ideal_range_score")
                    + 15.0 * pl.col("liquidity_score")
                ).alias("setup_score")
            )
            .with_columns(self._setup_pass_expr().alias("passes_setup_filter"))
            .with_columns(self._reject_reason_expr().alias("reject_reason"))
        )

    def _setup_pass_expr(self) -> pl.Expr:
        cfg = self.config
        return (
            (pl.col("box_close") >= cfg.min_price)
            & (pl.col("box_close") <= cfg.max_price)
            & (pl.col("avg_daily_volume_14") >= cfg.min_avg_daily_volume)
            & (pl.col("atr_14") >= cfg.min_atr)
            & (pl.col("orb_relative_volume") >= cfg.min_opening_relative_volume)
            & (pl.col("gap_pct") >= cfg.min_gap_up_pct)
            & (pl.col("box_close") > pl.col("box_open"))
            & (pl.col("range_atr") >= cfg.min_orb_range_atr_fraction)
            & (pl.col("range_atr") <= cfg.max_orb_range_atr_fraction)
            & (pl.col("close_location") >= cfg.min_close_location)
            & (pl.col("body_to_range") >= cfg.min_body_to_range)
            & (pl.col("setup_score") >= cfg.min_setup_score)
        ).fill_null(False)

    def _reject_reason_expr(self) -> pl.Expr:
        cfg = self.config
        return (
            pl.when(pl.col("box_close") < cfg.min_price).then(pl.lit("price_low"))
            .when(pl.col("box_close") > cfg.max_price).then(pl.lit("price_high"))
            .when(pl.col("avg_daily_volume_14") < cfg.min_avg_daily_volume).then(pl.lit("liquidity"))
            .when(pl.col("atr_14") < cfg.min_atr).then(pl.lit("atr"))
            .when(pl.col("orb_relative_volume") < cfg.min_opening_relative_volume).then(pl.lit("relative_volume"))
            .when(pl.col("gap_pct") < cfg.min_gap_up_pct).then(pl.lit("gap"))
            .when(pl.col("range_atr") < cfg.min_orb_range_atr_fraction).then(pl.lit("range_small"))
            .when(pl.col("range_atr") > cfg.max_orb_range_atr_fraction).then(pl.lit("range_large"))
            .when(pl.col("close_location") < cfg.min_close_location).then(pl.lit("close_location"))
            .when(pl.col("body_to_range") < cfg.min_body_to_range).then(pl.lit("body"))
            .when(pl.col("setup_score") < cfg.min_setup_score).then(pl.lit("score"))
            .otherwise(pl.lit("passed"))
        )

    def _live_candidates(
        self,
        context: MinuteContext,
        portfolio: Portfolio,
        pending_orders: list[Order],
    ) -> list[dict]:
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        candidates = []
        live_rows = []
        for ticker, setup in self.watchlist.items():
            bar = context.bars_by_symbol.get(ticker)
            if bar is None:
                continue

            trigger = self.entry_trigger(setup)
            stop = self.protective_stop_price(setup)
            last_price = float(bar["close"])
            live_score = self.live_score(setup, bar)
            status = "eligible"
            reason = ""

            if ticker in portfolio.positions:
                status = "held"
                reason = "already_held"
            elif ticker in pending_symbols:
                status = "pending"
                reason = "entry_pending"
            elif not self.breakout_armed.get(ticker, True):
                status = "inactive"
                reason = "not_armed"
            elif last_price < stop:
                status = "invalid"
                reason = "lost_breakout_zone"
            elif last_price > trigger:
                status = "invalid"
                reason = "missed_breakout"
            elif not self._macd_open(bar):
                status = "invalid"
                reason = "macd_closed"
            elif not self._tema_open(bar, setup):
                status = "invalid"
                reason = "tema_closed"
            elif live_score < self.config.min_live_score:
                status = "invalid"
                reason = "live_score"

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
                    "orb_relative_volume": setup.get("orb_relative_volume"),
                    "macd_line_5m": bar.get("macd_line_5m"),
                    "macd_signal_5m": bar.get("macd_signal_5m"),
                    "macd_hist_5m": bar.get("macd_hist_5m"),
                    "tema9_5m": bar.get("tema9_5m"),
                    "tema20_5m": bar.get("tema20_5m"),
                }
            )

            if status != "eligible":
                if status == "invalid":
                    self._reject(context.timestamp, ticker, reason, setup, bar, live_score if reason == "live_score" else None)
                continue

            if last_price <= trigger:
                self.breakout_armed[ticker] = True

            candidate = {
                **setup,
                "timestamp": context.timestamp,
                "last_price": last_price,
                "live_score": live_score,
                "trigger": trigger,
                "stop": stop,
            }
            candidates.append(candidate)

        live_rows.sort(key=lambda item: float(item.get("live_score") or 0.0), reverse=True)
        for live_rank, row in enumerate(live_rows, start=1):
            row["live_rank"] = live_rank
            self.live_rankings.append(row)

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
        quantity = self.calculate_quantity(
            candidate["trigger"],
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
        tag = (
            f"ENTRY|type=STOP|rule=5M_BOX_LIVE_MACD_TEMA|rank={candidate['rank']}|lrank={live_rank}"
            f"|qty={quantity}|trigger={candidate['trigger']:.2f}|stop={candidate['stop']:.2f}"
            f"|box_high={candidate['box_high']:.2f}|box_mid={candidate['box_mid']:.2f}|box_low={candidate['box_low']:.2f}"
            f"|setup={candidate['setup_score']:.1f}|live={candidate['live_score']:.1f}"
            f"|rv={candidate['orb_relative_volume']:.1f}|rp={risk_pct * 100:.2f}"
        )
        return OrderRequest(
            symbol=candidate["ticker"],
            side="BUY",
            quantity=quantity,
            order_type="STOP",
            stop_price=candidate["trigger"],
            reason="LIVE_SIGNAL",
            tag=tag,
        )

    def _position_exit_requests(self, context: MinuteContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        for symbol, position in list(portfolio.positions.items()):
            bar = context.bars_by_symbol.get(symbol)
            if bar is None:
                continue

            if float(bar["low"]) <= position.stop_price:
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="STOP",
                        stop_price=position.stop_price,
                        reason="BREAKOUT_FAIL",
                        tag=self._exit_tag("BREAKOUT_FAIL", position, bar),
                        allow_same_bar_fill=True,
                    )
                )
                continue

            setup = self.watchlist.get(symbol)
            if setup is not None and self._tema_closed(bar, setup):
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="TEMA_CLOSE",
                        tag=self._exit_tag("TEMA_CLOSE", position, bar),
                    )
                )
        return requests

    def _replacement_candidate(self, live_candidates: list[dict], portfolio: Portfolio, timestamp: datetime):
        weakest = None
        for symbol, position in portfolio.positions.items():
            held_minutes = (timestamp - position.entry_time).total_seconds() / 60.0
            if held_minutes < self.config.minimum_hold_minutes:
                continue
            score = position.live_score
            if weakest is None or score < weakest[0]:
                weakest = (score, symbol)
        if weakest is None:
            return None
        for candidate in live_candidates:
            if candidate["live_score"] > weakest[0] + self.config.replacement_score_buffer:
                return candidate, weakest[1]
        return None

    def _occupied_slot_count(self, portfolio: Portfolio, pending_orders: list[Order]) -> int:
        pending_entries = len([order for order in pending_orders if order.side == "BUY" and order.status == "OPEN"])
        return len(portfolio.positions) + pending_entries

    def _macd_open(self, bar: dict) -> bool:
        return (
            bool(bar.get("macd_ready_5m"))
            and bar.get("macd_line_5m") is not None
            and bar.get("macd_signal_5m") is not None
            and bar.get("macd_hist_5m") is not None
            and float(bar["macd_line_5m"]) > float(bar["macd_signal_5m"])
            and float(bar["macd_line_5m"]) > 0
            and float(bar["macd_hist_5m"]) > 0
        )

    def _tema_open(self, bar: dict, setup: dict) -> bool:
        return (
            bool(bar.get("tema_ready_5m"))
            and bar.get("tema9_5m") is not None
            and bar.get("tema20_5m") is not None
            and float(bar["tema9_5m"]) > float(bar["tema20_5m"]) + self.tema_entry_buffer(setup)
        )

    def _tema_closed(self, bar: dict, setup: dict) -> bool:
        return (
            bool(bar.get("tema_ready_5m"))
            and bar.get("tema9_5m") is not None
            and bar.get("tema20_5m") is not None
            and float(bar["tema20_5m"]) + self.tema_exit_buffer(setup) > float(bar["tema9_5m"])
        )

    def live_score(self, setup: dict, bar: dict) -> float:
        last_price = float(bar["close"])
        if last_price <= 0:
            return float(setup["setup_score"])
        macd_strength = min(float(bar.get("macd_hist_5m") or 0.0) / last_price * 1000.0, 20.0)
        tema_spread = max(0.0, float(bar.get("tema9_5m") or 0.0) - float(bar.get("tema20_5m") or 0.0))
        tema_strength = min(tema_spread / last_price * 1000.0, 20.0)
        extension = max(0.0, (last_price / self.entry_trigger(setup)) - 1.0)
        extension_score = min(extension / 0.05, 1.0) * 10.0
        return float(setup["setup_score"]) + macd_strength + tema_strength + extension_score

    def risk_pct_for_score(self, score_quality: float) -> float:
        return self.config.min_risk_pct + ((self.config.max_risk_pct - self.config.min_risk_pct) * score_quality)

    def calculate_quantity(
        self,
        entry: float,
        stop: float,
        score_quality: float,
        risk_pct: float,
        live_candidate_count: int,
        portfolio: Portfolio,
    ) -> int:
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0 or entry <= 0:
            return 0
        total_equity = portfolio.total_equity()
        deployable_cash = max(0.0, portfolio.cash - (total_equity * self.config.cash_reserve_pct))
        open_slots = max(1, self.config.max_active_positions - len(portfolio.positions))
        allocation_slots = max(1, min(open_slots, live_candidate_count))
        base_capital_budget = deployable_cash / allocation_slots
        capital_multiplier = 0.75 + (0.50 * score_quality)
        capital_budget = min(
            deployable_cash,
            total_equity * self.config.max_capital_per_trade_pct,
            base_capital_budget * capital_multiplier,
        )
        quantity_by_risk = int((total_equity * risk_pct) / risk_per_share)
        quantity_by_cash = int(capital_budget / entry)
        quantity_by_available_cash = int(deployable_cash / entry)
        return max(0, min(quantity_by_risk, quantity_by_cash, quantity_by_available_cash))

    def entry_trigger(self, setup: dict) -> float:
        return float(setup["box_high"]) * (1.0 + self.config.entry_buffer_pct)

    def protective_stop_price(self, setup: dict) -> float:
        return float(setup["box_high"]) - (
            self.config.stop_box_pullback_fraction * (float(setup["box_high"]) - float(setup["box_mid"]))
        )

    def tema_entry_buffer(self, setup: dict) -> float:
        return float(setup.get("atr_14") or 0.0) * self.config.tema_entry_atr_buffer

    def tema_exit_buffer(self, setup: dict) -> float:
        return float(setup.get("atr_14") or 0.0) * self.config.tema_exit_atr_buffer

    def _exit_tag(self, reason: str, position, bar: dict) -> str:
        return (
            f"EXIT|reason={reason}|price={float(bar['close']):.2f}|stop={position.stop_price:.2f}"
            f"|maxp={position.max_price:.2f}|maxu={position.max_unrealized_profit:.2f}|maxR={position.max_r_multiple:.2f}"
            f"|tema9={float(bar.get('tema9_5m') or 0.0):.4f}|tema20={float(bar.get('tema20_5m') or 0.0):.4f}"
        )

    def _reject(self, timestamp: datetime, ticker: str, reason: str, setup: dict, bar: dict, live_score: float | None = None):
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": ticker,
                "reject_reason": reason,
                "setup_rank": setup.get("rank"),
                "setup_score": setup.get("setup_score"),
                "live_score": live_score,
                "price": float(bar["close"]),
            }
        )
