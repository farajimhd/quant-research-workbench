from AlgorithmImports import *


class SymbolState:

    def __init__(self, symbol: Symbol):
        self.symbol = symbol

        self.avg_daily_volume_14 = None
        self.atr_14 = None
        self.previous_close = None

        self.orb_date = None
        self.orb_open = None
        self.orb_high = None
        self.orb_low = None
        self.orb_close = None
        self.orb_volume = 0.0
        self.orb_relative_volume = 0.0
        self.orb_direction = None
        self.orb_ranked = False
        self.orb_score = 0.0
        self.breakout_armed = True

        self.orb_entry_order_id = None
        self.orb_stop_order_id = None
        self.orb_entry_price = None
        self.orb_stop_price = None
        self.orb_quantity = 0
        self.orb_exit_submitted = False
        self.orb_rank = None

        self.last_price = None
        self.previous_price = None
        self.last_high = None
        self.last_low = None

        self.macd_bucket = None
        self.macd_bucket_close = None
        self.macd_fast_ema = None
        self.macd_slow_ema = None
        self.macd_signal = None
        self.macd_line = None
        self.macd_hist = None
        self.prev_macd_line = None
        self.prev_macd_signal = None
        self.macd_fast_count = 0
        self.macd_slow_count = 0
        self.macd_signal_count = 0
        self.macd_ready = False

        self.tema9 = None
        self.tema9_ema1 = None
        self.tema9_ema2 = None
        self.tema9_ema3 = None
        self.tema9_count = 0
        self.tema20 = None
        self.tema20_ema1 = None
        self.tema20_ema2 = None
        self.tema20_ema3 = None
        self.tema20_count = 0
        self.tema_ready = False

    def reset_orb_day(self, current_date):
        self.orb_date = current_date
        self.orb_open = None
        self.orb_high = None
        self.orb_low = None
        self.orb_close = None
        self.orb_volume = 0.0
        self.orb_relative_volume = 0.0
        self.orb_direction = None
        self.orb_ranked = False
        self.orb_score = 0.0
        self.breakout_armed = True

        self.orb_entry_order_id = None
        self.orb_stop_order_id = None
        self.orb_entry_price = None
        self.orb_stop_price = None
        self.orb_quantity = 0
        self.orb_exit_submitted = False
        self.orb_rank = None

        self.last_price = None
        self.previous_price = None
        self.last_high = None
        self.last_low = None
        self.macd_bucket = None
        self.macd_bucket_close = None
