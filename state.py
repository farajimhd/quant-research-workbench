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

        self.orb_entry_order_id = None
        self.orb_stop_order_id = None
        self.orb_profit_order_id = None
        self.orb_entry_price = None
        self.orb_stop_price = None
        self.orb_profit_price = None
        self.orb_quantity = 0
        self.orb_exit_submitted = False
        self.orb_rank = None
        self.orb_reentry_level = None
        self.orb_reentry_count = 0

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

        self.orb_entry_order_id = None
        self.orb_stop_order_id = None
        self.orb_profit_order_id = None
        self.orb_entry_price = None
        self.orb_stop_price = None
        self.orb_profit_price = None
        self.orb_quantity = 0
        self.orb_exit_submitted = False
        self.orb_rank = None
        self.orb_reentry_level = None
        self.orb_reentry_count = 0
