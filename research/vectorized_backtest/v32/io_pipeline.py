"""Shared certified data/execution infrastructure; no V27 genetics."""
from research.vectorized_backtest.v27.io_pipeline import *
from research.vectorized_backtest.v27.io_pipeline import Publisher as _Publisher
from .publication import write_json


class Publisher(_Publisher):
    """Same bounded queue and error propagation, compact V32 artifacts."""
    def write(self, path, value):
        if len(self.pending) >= self.capacity:
            self.pending.popleft().result()
        self.pending.append(self.pool.submit(write_json, path, value))
