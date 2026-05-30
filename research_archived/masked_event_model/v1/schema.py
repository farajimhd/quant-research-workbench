from __future__ import annotations

QUOTE_FEATURE_COLUMNS: tuple[str, ...] = (
    "time_offset",
    "delta_time",
    "bid_price",
    "ask_price",
    "mid_price",
    "spread_bps",
    "bid_size",
    "ask_size",
    "quote_imbalance",
    "bid_exchange",
    "ask_exchange",
    "tape",
    "condition_count",
    "condition_first",
    "indicator_count",
    "indicator_first",
    "participant_latency_ms",
    "trf_latency_ms",
)

TRADE_FEATURE_COLUMNS: tuple[str, ...] = (
    "time_offset",
    "delta_time",
    "price",
    "size",
    "exchange",
    "latest_bid",
    "latest_ask",
    "latest_mid",
    "latest_spread_bps",
    "latest_quote_imbalance",
    "price_vs_mid_bps",
    "side_proxy",
    "tape",
    "condition_count",
    "condition_first",
    "correction",
    "trade_id",
    "trf_id",
    "participant_latency_ms",
    "trf_latency_ms",
)

CHUNK_SUMMARY_COLUMNS: tuple[str, ...] = (
    "event_count",
    "quote_count",
    "trade_count",
    "overflow_quote_count",
    "overflow_trade_count",
    "overflow_total_count",
    "overflow_trade_volume",
    "overflow_signed_volume",
    "overflow_mid_min",
    "overflow_mid_max",
    "overflow_spread_min_bps",
    "overflow_spread_max_bps",
    "latest_bid",
    "latest_ask",
    "latest_mid",
    "latest_spread_bps",
    "latest_bid_size",
    "latest_ask_size",
    "latest_quote_imbalance",
    "trade_volume",
    "signed_trade_volume",
    "seconds_since_trade",
    "seconds_since_quote",
    "has_trade",
    "has_quote",
)

QUOTE_PRICE_COLUMNS = {"bid_price", "ask_price", "mid_price"}
TRADE_PRICE_COLUMNS = {"price", "latest_bid", "latest_ask", "latest_mid"}
SUMMARY_PRICE_COLUMNS = {"overflow_mid_min", "overflow_mid_max", "latest_bid", "latest_ask", "latest_mid"}

LOG_COLUMNS = {
    "bid_size",
    "ask_size",
    "size",
    "trade_id",
    "event_count",
    "quote_count",
    "trade_count",
    "overflow_quote_count",
    "overflow_trade_count",
    "overflow_total_count",
    "overflow_trade_volume",
    "trade_volume",
    "latest_bid_size",
    "latest_ask_size",
}

EVENT_KIND_PAD = 2
TARGET_PREFIX = "target_mid_h"
