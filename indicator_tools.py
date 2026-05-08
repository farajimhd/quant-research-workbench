from AlgorithmImports import *


class FiveMinuteIndicatorMixin:

    def configure_indicators(self):
        self.macd_fast_period = 12
        self.macd_slow_period = 26
        self.macd_signal_period = 9
        self.macd_fast_alpha = 2.0 / (self.macd_fast_period + 1.0)
        self.macd_slow_alpha = 2.0 / (self.macd_slow_period + 1.0)
        self.macd_signal_alpha = 2.0 / (self.macd_signal_period + 1.0)
        self.tema9_period = 9
        self.tema20_period = 20
        self.tema9_alpha = 2.0 / (self.tema9_period + 1.0)
        self.tema20_alpha = 2.0 / (self.tema20_period + 1.0)

    def update_five_minute_indicators(self, state, bar):
        minutes = self.minutes_since_midnight()

        if minutes < 9 * 60 + 30 or minutes >= 16 * 60:
            return

        self.update_five_minute_indicators_at(state, float(bar.Close), minutes)

    def update_five_minute_indicators_at(self, state, close, minutes):
        bucket = minutes // 5

        if state.macd_bucket is None:
            state.macd_bucket = bucket
            state.macd_bucket_close = close
            return

        if bucket == state.macd_bucket:
            state.macd_bucket_close = close
            return

        self.update_macd_from_close(state, state.macd_bucket_close)
        self.update_tema_from_close(state, state.macd_bucket_close)
        state.macd_bucket = bucket
        state.macd_bucket_close = close

    def warm_up_indicators(self, symbol, state):
        if state.macd_ready and state.tema_ready:
            return

        history = self.algorithm.History(symbol, 220, Resolution.Minute)

        if history is None or history.empty:
            return

        history = history.reset_index()
        columns = {str(column).lower(): column for column in history.columns}

        if "close" not in columns:
            return

        time_column = None

        for candidate in ["time", "endtime"]:
            if candidate in columns:
                time_column = columns[candidate]
                break

        if time_column is None:
            return

        for _, row in history.iterrows():
            try:
                bar_time = row[time_column]
                minutes = bar_time.hour * 60 + bar_time.minute
                close = float(row[columns["close"]])
            except Exception:
                continue

            if minutes < 9 * 60 + 30 or minutes >= 16 * 60:
                continue

            self.update_five_minute_indicators_at(state, close, minutes)

    def update_macd_from_close(self, state, close):
        if state.macd_fast_ema is None:
            state.macd_fast_ema = close
            state.macd_slow_ema = close
            state.macd_fast_count = 1
            state.macd_slow_count = 1
            return

        state.macd_fast_ema = (
            self.macd_fast_alpha * close
            + (1.0 - self.macd_fast_alpha) * state.macd_fast_ema
        )
        state.macd_slow_ema = (
            self.macd_slow_alpha * close
            + (1.0 - self.macd_slow_alpha) * state.macd_slow_ema
        )
        state.macd_fast_count += 1
        state.macd_slow_count += 1

        if state.macd_slow_count < self.macd_slow_period:
            return

        macd_line = state.macd_fast_ema - state.macd_slow_ema
        state.prev_macd_line = state.macd_line
        state.prev_macd_signal = state.macd_signal
        state.macd_line = macd_line

        if state.macd_signal is None:
            state.macd_signal = macd_line
            state.macd_signal_count = 1
        else:
            state.macd_signal = (
                self.macd_signal_alpha * macd_line
                + (1.0 - self.macd_signal_alpha) * state.macd_signal
            )
            state.macd_signal_count += 1

        state.macd_hist = state.macd_line - state.macd_signal
        state.macd_ready = state.macd_signal_count >= self.macd_signal_period

    def update_tema_from_close(self, state, close):
        state.tema9_ema1, state.tema9_ema2, state.tema9_ema3, state.tema9 = (
            self.update_tema_values(
                close,
                self.tema9_alpha,
                state.tema9_ema1,
                state.tema9_ema2,
                state.tema9_ema3,
            )
        )
        state.tema20_ema1, state.tema20_ema2, state.tema20_ema3, state.tema20 = (
            self.update_tema_values(
                close,
                self.tema20_alpha,
                state.tema20_ema1,
                state.tema20_ema2,
                state.tema20_ema3,
            )
        )
        state.tema9_count += 1
        state.tema20_count += 1
        state.tema_ready = (
            state.tema9_count >= self.tema9_period
            and state.tema20_count >= self.tema20_period
        )

    def update_tema_values(self, close, alpha, ema1, ema2, ema3):
        if ema1 is None:
            ema1 = close
            ema2 = close
            ema3 = close
        else:
            ema1 = alpha * close + (1.0 - alpha) * ema1
            ema2 = alpha * ema1 + (1.0 - alpha) * ema2
            ema3 = alpha * ema2 + (1.0 - alpha) * ema3

        tema = (3.0 * ema1) - (3.0 * ema2) + ema3
        return ema1, ema2, ema3, tema
