"""Offline Polars oracle for source-history semantics and full trajectory checks.

This adapter is used only in validation, never by the tensor replay hot path.
It lowers source windows to precomputed causal columns, preserving the existing
Polars account simulator as an independent broker/accounting reference.
"""

from dataclasses import replace

import polars as pl

from research.vectorized_backtest.v1.strategy_encoding import AtomicInput, Operation
from research.vectorized_backtest.v1.strategy_encoding import (
    compile_strategy as polars_compile,
)
from research.vectorized_backtest.v1.strategy_encoding.replay import evaluate

from .compiler import HistoryOperation, compile_strategy


def evaluate_reference(program, catalog, prepared, broker):
    strategy = compile_strategy(program, catalog)
    rows = list(program.instructions)
    extra = []
    features = dict(prepared.features)
    for index, operation, field, parameter, _ in strategy.source_windows:
        size = int(program.values[parameter])
        frame = features[field.resolution_ms]
        source_clock = field.available_at_column
        source = frame.filter(pl.col(source_clock).is_not_null()).sort(
            ["listing_id", "time_us"]
        )
        value = pl.col(field.column).cast(pl.Float64) * field.scale
        valid = value.is_finite() & (pl.col(source_clock) <= pl.col("time_us"))
        if field.valid_column:
            valid &= pl.col(field.valid_column).cast(pl.Boolean).fill_null(False)
        source = source.with_columns(
            pl.when(valid).then(value).otherwise(None).alias("__value")
        )
        value = pl.col("__value")
        result = (
            value.shift(size)
            if operation == HistoryOperation.BAR_LAG
            else value.rolling_min(size, min_samples=size)
            if operation == HistoryOperation.BAR_MIN
            else value.rolling_max(size, min_samples=size)
            if operation == HistoryOperation.BAR_MAX
            else value.rolling_mean(size, min_samples=size)
            if operation == HistoryOperation.BAR_MEAN
            else value.rolling_sum(size, min_samples=size)
        )
        name = f"__source_history_{index}"
        source = source.select(
            "listing_id",
            "time_us",
            result.over("listing_id", order_by="time_us")
            .fill_null(float("nan"))
            .alias(name),
        )
        features[field.resolution_ms] = frame.join(
            source, on=["listing_id", "time_us"], how="left", validate="1:1"
        )
        label = 2**31 - 2 - index
        extra.append(AtomicInput(label, name, name, field.unit))
        rows[index] = (int(Operation.INPUT), label, 2**31 - 1, 2**31 - 1)
    lowered = replace(program, instructions=tuple(rows))
    compiled = polars_compile(
        lowered, replace(catalog, inputs=catalog.inputs + tuple(extra))
    )
    return evaluate(compiled, replace(prepared, features=features), broker)
