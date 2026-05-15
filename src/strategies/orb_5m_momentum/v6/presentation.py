from __future__ import annotations

from typing import Any

from src.strategies.orb_5m_momentum.v2.presentation import chart_presentation as v2_chart_presentation


def chart_presentation() -> dict[str, Any]:
    presentation = v2_chart_presentation()
    presentation["default_visible"] = ["indicator.vwap"]
    presentation["trade_annotations"] = {
        "entry_label": "quantity",
        "exit_label": "quantity",
        "show_entry_line": True,
        "show_exit_line": True,
        "show_stop_line": True,
        "show_trigger_line": True,
        "show_trade_region": True,
    }
    return presentation
