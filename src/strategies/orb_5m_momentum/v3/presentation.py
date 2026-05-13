from __future__ import annotations

from typing import Any


def chart_presentation() -> dict[str, Any]:
    timeframes = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
    intraday_timeframes = ["1m", "5m", "15m", "30m", "1h", "2h", "4h"]
    return {
        "schema_version": 1,
        "kind": "strategy_trade_chart",
        "default_timeframe": "1m",
        "timeframes": timeframes,
        "default_visible": ["feature.opening_range_5m"],
        "display_items": {
            timeframe: ([] if timeframe not in intraday_timeframes else ["feature.opening_range_5m"])
            for timeframe in timeframes
        },
        "feature_groups": {
            timeframe: (["session"] if timeframe in intraday_timeframes else [])
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
