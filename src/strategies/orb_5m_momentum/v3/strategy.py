from __future__ import annotations

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order
from src.backtest.portfolio import Portfolio
from src.strategies.orb_5m_momentum.v2.strategy import OrbFiveMinuteMomentumV2Strategy
from src.strategies.orb_5m_momentum.v3.config import OrbMomentumConfig
from src.strategies.orb_5m_momentum.v3.presentation import chart_presentation


class OrbFiveMinuteMomentumV3Strategy(OrbFiveMinuteMomentumV2Strategy):
    name = "orb_5m_momentum"

    def __init__(self, config: OrbMomentumConfig | None = None):
        super().__init__(config or OrbMomentumConfig())
        self.live_trend_scores: dict[str, dict] = {}

    def data_requirements(self) -> DataRequirements:
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=("core", "session", "momentum"),
            context_feature_groups={"5m": ("momentum",)},
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
                "macd_line",
                "macd_signal",
                "macd_hist",
            ),
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> pl.DataFrame:
        self.live_trend_scores = {}
        return super().prepare_day(frames, portfolio)

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]):
        self._update_live_trend_scores(context)
        return super().on_bar(context, portfolio, pending_orders)

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
                pl.when(pl.col("box_open") > 0)
                .then(pl.col("box_range") / pl.col("box_open"))
                .otherwise(0.0)
                .alias("box_range_pct"),
                pl.when(pl.col("box_low") > 0)
                .then(pl.col("box_range") / pl.col("box_low"))
                .otherwise(0.0)
                .alias("box_strength"),
            )
        )

        macd_pressure = self._opening_1m_macd_pressure_frame(frames)
        if macd_pressure.is_empty():
            box = box.with_columns(pl.lit(0.0).alias("opening_macd_pressure_bps"))
        else:
            box = box.join(macd_pressure, on="ticker", how="left").with_columns(
                pl.col("opening_macd_pressure_bps").fill_null(0.0)
            )

        return (
            box.with_columns(
                pl.col("opening_macd_pressure_bps").alias("macd_pressure_bps"),
                pl.col("opening_macd_pressure_bps").alias("session_macd_pressure_bps"),
                pl.col("opening_macd_pressure_bps").alias("trend_score"),
                pl.col("opening_macd_pressure_bps").alias("setup_score"),
            )
            .with_columns(self._setup_pass_expr().alias("passes_setup_filter"))
            .with_columns(self._reject_reason_expr().alias("reject_reason"))
        )

    def _opening_1m_macd_pressure_frame(self, frames: DayFrames) -> pl.DataFrame:
        frame = frames.event_frame
        required = {"ticker", "minute_of_day", "close", "macd_line", "macd_signal"}
        if not required.issubset(set(frame.columns)):
            return pl.DataFrame({"ticker": [], "opening_macd_pressure_bps": []})

        cfg = self.config
        return (
            self._session_frame(frame)
            .filter(
                (pl.col("minute_of_day") >= cfg.opening_box_start_minute)
                & (pl.col("minute_of_day") < cfg.opening_box_end_minute)
            )
            .with_columns(
                pl.when(pl.col("close") > 0)
                .then(((pl.col("macd_line") - pl.col("macd_signal")) / pl.col("close")) * 10_000.0)
                .otherwise(0.0)
                .alias("macd_pressure_bps")
            )
            .group_by("ticker")
            .agg(pl.col("macd_pressure_bps").sum().alias("opening_macd_pressure_bps"))
        )

    def _update_live_trend_scores(self, context: BarContext) -> None:
        for bar in context.updates.iter_rows(named=True):
            ticker = bar.get("ticker")
            if ticker not in self.watchlist:
                continue
            close = float(bar.get("close") or 0.0)
            if close <= 0:
                continue

            macd_hist = bar.get("macd_hist")
            if macd_hist is None and bar.get("macd_line") is not None and bar.get("macd_signal") is not None:
                macd_hist = float(bar["macd_line"]) - float(bar["macd_signal"])
            macd_pressure_bps = (float(macd_hist or 0.0) / close) * 10_000.0

            setup = self.watchlist[ticker]
            previous = self.live_trend_scores.get(ticker, {})
            cumulative_macd_pressure_bps = float(previous.get("cumulative_macd_pressure_bps") or 0.0) + macd_pressure_bps
            box_close = float(setup.get("box_close") or 0.0)
            price_trend_bps = ((close / box_close) - 1.0) * 10_000.0 if box_close > 0 else 0.0
            trend_score = (
                self.config.trend_macd_weight * cumulative_macd_pressure_bps
                + self.config.trend_price_weight * price_trend_bps
            )
            trend = {
                "macd_pressure_bps": macd_pressure_bps,
                "cumulative_macd_pressure_bps": cumulative_macd_pressure_bps,
                "session_macd_pressure_bps": cumulative_macd_pressure_bps,
                "price_trend_bps": price_trend_bps,
                "trend_score": trend_score,
                "setup_score": trend_score,
            }
            self.live_trend_scores[ticker] = trend
            setup.update(trend)

    def live_score(self, setup: dict, bar: dict) -> float:
        last_price = float(bar["close"])
        trend_score = float(setup.get("trend_score", setup.get("setup_score", 0.0)) or 0.0)
        if last_price <= 0:
            return trend_score
        macd_strength = min(float(bar.get("macd_hist_5m") or 0.0) / last_price * 1000.0, 20.0)
        tema_spread = max(0.0, float(bar.get("tema9_5m") or 0.0) - float(bar.get("tema20_5m") or 0.0))
        tema_strength = min(tema_spread / last_price * 1000.0, 20.0)
        extension = max(0.0, (last_price / self.entry_trigger(setup)) - 1.0)
        extension_score = min(extension / 0.05, 1.0) * 10.0
        return trend_score + macd_strength + tema_strength + extension_score

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
