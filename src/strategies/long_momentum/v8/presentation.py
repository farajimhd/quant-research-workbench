from __future__ import annotations

from typing import Any

from src.strategies.long_momentum.v3.presentation import chart_presentation as v3_chart_presentation


def chart_presentation() -> dict[str, Any]:
    presentation = v3_chart_presentation()
    intraday_timeframes = {"1m", "5m", "15m", "30m", "1h", "2h", "4h"}
    presentation["default_visible"] = [
        "indicator.vwap",
        "indicator.tema_trend",
        "indicator.macd",
        "feature.price_volume_shock",
    ]
    for timeframe, groups in presentation.get("feature_groups", {}).items():
        if timeframe in intraday_timeframes and "shock" not in groups:
            groups.append("shock")
    for timeframe, items in presentation.get("display_items", {}).items():
        if timeframe in intraday_timeframes and "feature.price_volume_shock" not in items:
            items.append("feature.price_volume_shock")
    return presentation


__all__ = ["chart_presentation"]
