"""Encoded strategies and a causal, device-resident Torch broker simulator."""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from research.vectorized_backtest.v1.strategy_encoding import (
    Broker,
    Catalog,
    Funnel,
    Program,
    Session,
    arte_catalog,
)
from research.vectorized_backtest.v1.strategy_encoding.clickhouse import prepare_session

from .compiler import HistoryOperation, TorchStrategy, compile_strategy
from .data import TensorTape, to_tensors
from .replay import ReplayRunner
from .vocabulary import describe

__all__ = [
    "Broker",
    "Catalog",
    "Funnel",
    "HistoryOperation",
    "Program",
    "ReplayRunner",
    "Session",
    "TensorTape",
    "TorchStrategy",
    "arte_catalog",
    "compile_strategy",
    "describe",
    "prepare_session",
    "to_tensors",
]
