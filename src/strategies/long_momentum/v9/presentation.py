from __future__ import annotations

from typing import Any

from src.strategies.long_momentum.v3.presentation import chart_presentation as v3_chart_presentation


def chart_presentation() -> dict[str, Any]:
    presentation = v3_chart_presentation()
    intraday_timeframes = {"1m", "5m", "15m", "30m", "1h", "2h", "4h"}
    presentation["default_visible"] = [
        "indicator.vwap",
        "indicator.tema_trend",
        "feature.volume_liquidity.double_timeframe_bearish_volume_divergence",
    ]
    for timeframe, groups in presentation.get("feature_groups", {}).items():
        if timeframe in intraday_timeframes and "volume_liquidity" not in groups:
            groups.append("volume_liquidity")
    for timeframe, items in presentation.get("display_items", {}).items():
        if timeframe in intraday_timeframes and "feature.volume_liquidity.double_timeframe_bearish_volume_divergence" not in items:
            items.append("feature.volume_liquidity.double_timeframe_bearish_volume_divergence")
    return presentation


__all__ = ["chart_presentation"]
