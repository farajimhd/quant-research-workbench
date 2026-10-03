from dataclasses import replace
from uuid import uuid4
import polars as pl
import pytest
from pipelines.strategy_one.strategy_forty_five_liquidity import derive_windows
from src.trading_runtime.strategy_forty_five_liquidity import passes, breached, MINIMUM_VOLUME_300S
from src.trading_runtime.strategy_forty_five_rules import entry_geometry
from tests.test_strategy_forty_five_rules import facts


def derive(bars):
    return derive_windows(bars, ticker="TEST", session_end_ms=600000,
        build="build", day="2026-09-03", attempt="00000000-0000-0000-0000-000000000001")


def test_exact_completed_windows_and_future_suffix_invariance():
    bars = pl.DataFrame({"boundary_ms": range(30000,600001,30000),
        "volume": [float(i) for i in range(1,21)], "trade_count": range(1,21)})
    full = derive(bars)
    prefix = derive(bars.filter(pl.col("boundary_ms") <= 300000))
    assert full.filter(pl.col("boundary_ms") <= 300000).equals(prefix.filter(pl.col("boundary_ms") <= 300000))
    row = full.row(9, named=True)
    assert (row["trades_60s"], row["volume_300s"],row["history_ready"]) == (19,55.,1)
    assert full.row(8,named=True)["volume_300s"] is None


def test_sparse_certified_candles_are_zero_activity_and_invalid_rows_rejected():
    bars = pl.DataFrame({"boundary_ms":[300000],"volume":[20.],"trade_count":[3]})
    row = derive(bars).row(9,named=True)
    assert (row["trades_60s"],row["volume_300s"]) == (3,20.)
    with pytest.raises(ValueError,match="Duplicate"):
        derive(pl.concat([bars,bars]))
    with pytest.raises(ValueError,match="Invalid"):
        derive(bars.with_columns(pl.lit(-1.).alias("volume")))


def test_entry_equality_passes_and_each_strict_breach_blocks():
    base = facts()
    exact = replace(base.liquidity,trades_60s=59,volume_300s=MINIMUM_VOLUME_300S)
    assert passes(exact) and not breached(exact)
    assert entry_geometry(replace(base,liquidity=exact),already_submitted=False)
    for lower in (replace(exact,trades_60s=58),replace(exact,volume_300s=MINIMUM_VOLUME_300S-1)):
        assert breached(lower) and not passes(lower)
        assert entry_geometry(replace(base,liquidity=lower),already_submitted=False) is None
