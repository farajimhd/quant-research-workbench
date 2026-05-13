from __future__ import annotations

from typing import Any

from src.strategies.orb_5m_momentum.v2.presentation import chart_presentation as v2_chart_presentation


def chart_presentation() -> dict[str, Any]:
    return v2_chart_presentation()
