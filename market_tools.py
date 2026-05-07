from AlgorithmImports import *
from datetime import timedelta


# =============================================================================
# Market Structure, Liquidity, and Price Action Helpers
# =============================================================================

class MarketTools:

    @staticmethod
    def is_market_open(
        algorithm: QCAlgorithm,
        symbol: Symbol,
        extended_market_hours: bool = False,
    ) -> bool:
        if symbol not in algorithm.Securities:
            return False

        security = algorithm.Securities[symbol]

        try:
            return bool(
                security.Exchange.Hours.IsOpen(
                    algorithm.Time - timedelta(minutes=1),
                    algorithm.Time,
                    extended_market_hours,
                )
            )
        except Exception:
            return False

    @staticmethod
    def spread_pct(algorithm: QCAlgorithm, symbol: Symbol, price: float) -> float:
        security = algorithm.Securities[symbol]

        bid = security.BidPrice
        ask = security.AskPrice

        if bid is None or ask is None or bid <= 0 or ask <= 0 or price <= 0:
            return 0.0

        return float((ask - bid) / price)

    @staticmethod
    def bid_ask(algorithm: QCAlgorithm, symbol: Symbol):
        security = algorithm.Securities[symbol]

        bid = float(security.BidPrice)
        ask = float(security.AskPrice)

        if bid <= 0 or ask <= 0 or ask < bid:
            return None, None

        return bid, ask

    @staticmethod
    def is_regular_market_open(algorithm: QCAlgorithm, symbol: Symbol) -> bool:
        return MarketTools.is_market_open(
            algorithm,
            symbol,
            extended_market_hours=False,
        )

    @staticmethod
    def is_regular_market_order_safe(
        algorithm: QCAlgorithm,
        symbol: Symbol,
        close_buffer_minutes: int,
    ) -> bool:
        if not MarketTools.is_regular_market_open(algorithm, symbol):
            return False

        now = algorithm.Time
        minutes = now.hour * 60 + now.minute
        market_close_minutes = 16 * 60

        return minutes < market_close_minutes - close_buffer_minutes

    @staticmethod
    def highest_high(state, bars: int, exclude_current: bool = True) -> float:
        if len(state.bars) < 2:
            return 0.0

        items = list(state.bars)

        if exclude_current:
            items = items[:-1]

        items = items[-bars:]

        if len(items) == 0:
            return 0.0

        return max(float(bar.High) for bar in items)

    @staticmethod
    def lowest_low(state, bars: int, exclude_current: bool = False) -> float:
        if len(state.bars) == 0:
            return 0.0

        items = list(state.bars)

        if exclude_current:
            items = items[:-1]

        items = items[-bars:]

        if len(items) == 0:
            return 0.0

        return min(float(bar.Low) for bar in items)

    @staticmethod
    def candle_body_pct(bar: TradeBar) -> float:
        open_price = float(bar.Open)
        close_price = float(bar.Close)

        if open_price <= 0:
            return 0.0

        return abs(close_price - open_price) / open_price

    @staticmethod
    def close_location_value(bar: TradeBar) -> float:
        high = float(bar.High)
        low = float(bar.Low)
        close = float(bar.Close)

        if high <= low:
            return 0.5

        return (close - low) / (high - low)

    @staticmethod
    def is_strong_green_candle(
        bar: TradeBar,
        min_body_pct: float,
        min_close_location: float,
    ) -> bool:
        if bar.Close <= bar.Open:
            return False

        if MarketTools.candle_body_pct(bar) < min_body_pct:
            return False

        if MarketTools.close_location_value(bar) < min_close_location:
            return False

        return True

    @staticmethod
    def is_explosive_high_break(
        state,
        bar: TradeBar,
        lookback_bars: int,
        min_breakout_margin_pct: float,
        min_body_pct: float,
        min_close_location: float,
    ) -> bool:
        previous_high = MarketTools.highest_high(
            state=state,
            bars=lookback_bars,
            exclude_current=True,
        )

        if previous_high <= 0:
            return False

        close_price = float(bar.Close)
        high_price = float(bar.High)

        breakout_level = previous_high * (1.0 + min_breakout_margin_pct)

        if close_price <= breakout_level:
            return False

        if high_price <= breakout_level:
            return False

        if not MarketTools.is_strong_green_candle(
            bar=bar,
            min_body_pct=min_body_pct,
            min_close_location=min_close_location,
        ):
            return False

        return True

    @staticmethod
    def is_tight_consolidation(state, lookback_bars: int, max_range_pct: float) -> bool:
        if len(state.bars) < lookback_bars:
            return False

        high = MarketTools.highest_high(state, lookback_bars, exclude_current=False)
        low = MarketTools.lowest_low(state, lookback_bars, exclude_current=False)

        if low <= 0:
            return False

        return (high - low) / low <= max_range_pct

    @staticmethod
    def is_pullback_holding_structure(state, bar: TradeBar) -> bool:
        if state.expansion_base is None:
            return True

        return float(bar.Close) >= state.expansion_base

    @staticmethod
    def is_structural_failure(state, bar: TradeBar) -> bool:
        if len(state.bars) < 4:
            return False

        recent = list(state.bars)[-4:]

        closes = [float(b.Close) for b in recent]
        lows = [float(b.Low) for b in recent]

        two_red = recent[-1].Close < recent[-1].Open and recent[-2].Close < recent[-2].Open
        lost_pullback_low = state.last_pullback_low is not None and float(bar.Close) < state.last_pullback_low
        lower_lows = lows[-1] < lows[-2] < lows[-3]
        close_weak = closes[-1] < min(closes[-2], closes[-3])

        return two_red and (lost_pullback_low or lower_lows or close_weak)

    @staticmethod
    def calculate_chart_stop(
        state,
        entry_price: float,
        lookback_bars: int,
        stop_buffer_pct: float,
        min_stop_pct: float,
        max_stop_pct: float,
    ):
        if len(state.bars) < lookback_bars:
            return None

        recent_bars = list(state.bars)[-lookback_bars:]
        pullback_low = min(float(bar.Low) for bar in recent_bars)

        buffered_stop = pullback_low * (1.0 - stop_buffer_pct)
        stop_distance_pct = (entry_price - buffered_stop) / entry_price

        if stop_distance_pct < min_stop_pct:
            buffered_stop = entry_price * (1.0 - min_stop_pct)
            stop_distance_pct = min_stop_pct

        if stop_distance_pct > max_stop_pct:
            return None

        return buffered_stop
