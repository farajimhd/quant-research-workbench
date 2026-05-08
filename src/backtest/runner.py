from __future__ import annotations

from src.backtest.config import BacktestConfig
from src.backtest.engine import BacktestEngine
from src.strategies.registry import create_strategy


def run_backtest(config_dict: dict, progress_callback=None) -> dict:
    config = BacktestConfig.from_dict(config_dict)
    strategy = create_strategy(config.strategy_name, config.strategy_params)
    engine = BacktestEngine(config, strategy)
    return engine.run(progress_callback=progress_callback)
