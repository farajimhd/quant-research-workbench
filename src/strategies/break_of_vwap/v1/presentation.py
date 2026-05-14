from __future__ import annotations

from typing import Any


def chart_presentation() -> dict[str, Any]:
    timeframes = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
    intraday = ["1m", "5m", "15m", "30m", "1h", "2h", "4h"]
    visible = [
        "indicator.vwap",
        "indicator.reclaim_vwap",
        "indicator.tema_trend",
        "indicator.macd",
        "indicator.relative_dollar_volume20",
    ]
    return {
        "schema_version": 1,
        "kind": "strategy_trade_chart",
        "default_timeframe": "1m",
        "timeframes": timeframes,
        "default_visible": visible,
        "display_items": {
            timeframe: visible
            + (["indicator.trend_regime", "indicator.price_volume_shock"] if timeframe in intraday else [])
            for timeframe in timeframes
        },
        "feature_groups": {
            timeframe: [
                "core",
                "momentum",
                *(["session", "volume_liquidity", "price_action", "shock", "market_structure"] if timeframe in intraday else []),
            ]
            for timeframe in timeframes
        },
        "trade_annotations": {
            "entry_label": "quantity",
            "exit_label": "quantity",
            "show_entry_line": True,
            "show_exit_line": True,
            "show_stop_line": True,
            "show_trigger_line": True,
            "show_trade_region": True,
        },
    }
