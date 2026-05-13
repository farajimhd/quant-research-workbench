from __future__ import annotations


class BacktestCancelled(Exception):
    """Raised when a backtest job receives a stop request."""
