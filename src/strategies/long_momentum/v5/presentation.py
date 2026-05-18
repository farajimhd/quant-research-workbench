from __future__ import annotations

from typing import Any


def chart_presentation() -> dict[str, Any]:
    timeframes = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
    intraday_timeframes = ["1m", "5m", "15m", "30m", "1h", "2h", "4h"]
    default_intraday_items = [
        "indicator.vwap",
        "indicator.tema_trend",
        "indicator.macd",
        "feature.volume_profile",
        "feature.volume_liquidity.bearish_volume_divergence",
        "feature.volume_liquidity.bullish_volume_divergence",
    ]
    default_daily_items = ["indicator.vwap", "indicator.tema_trend", "indicator.macd", "feature.volume_profile"]
    return {
        "schema_version": 1,
        "kind": "strategy_trade_chart",
        "default_timeframe": "30m",
        "timeframes": timeframes,
        "default_visible": default_intraday_items,
        "display_items": {
            timeframe: default_intraday_items if timeframe in intraday_timeframes else default_daily_items
            for timeframe in timeframes
        },
        "feature_groups": {
            timeframe: [
                "core",
                "momentum",
                *(["session", "volume_liquidity"] if timeframe in intraday_timeframes else []),
            ]
            for timeframe in timeframes
        },
        "trade_annotations": {
            "entry_label": "quantity",
            "exit_label": "quantity",
            "show_entry_line": True,
            "show_exit_line": True,
            "show_stop_line": True,
            "show_trigger_line": False,
            "show_trade_region": True,
        },
    }

