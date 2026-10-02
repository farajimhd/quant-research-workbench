"""Input/launch/export boundaries with synthetic data; no database connections."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import torch

from research.vectorized_backtest.v2.torch_backtest import Candidate, SqueezeRunner, Settings
from research.vectorized_backtest.v2.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v2.torch_backtest.prepare import _align, dependencies, liquidity_sql
from research.vectorized_backtest.v2.torch_backtest.run_grid import export_ledger, export_orders
from research.vectorized_backtest.v2.torch_backtest.runtime import require_runtime


def test_synthetic_launcher_roundtrip_and_verified_resume(tmp_path, monkeypatch):
    """Exercise campaign artifacts/resume without historical data or the full grid."""
    from research.vectorized_backtest.v2.torch_backtest import run_grid as launch
    grid = [Candidate(entry="signal", positions=m) for m in (5, 10)]
    manifest = {"candidate_count": 2, "approval_digest": "synthetic-test-only"}
    monkeypatch.setattr(launch, "build_grid", lambda: list(grid))
    monkeypatch.setattr(launch, "grid_manifest", lambda settings: manifest)
    calls = []
    def prepare(*args, **kwargs):
        calls.append(True)
        return synthetic_tape(seconds=45)
    monkeypatch.setattr(launch, "prepare_tape", prepare)
    source = tmp_path / "synthetic-source.json"
    source.write_text('{}')
    runtime = tmp_path / "launcher"
    assert launch.main(["--runtime", str(runtime)]) == 0
    assert not calls  # Plan creation never connects to market products.
    args = ["--execute", "--approval-digest", "synthetic-test-only", "--runtime", str(runtime),
            "--manifest", str(source), "--ledger", str(source), "--dates", "2026-01-02",
            "--device", "cpu", "--backend", "eager", "--batch", "3"]
    assert launch.main(args) == 0
    run = next((runtime / "campaigns").iterdir())
    receipt = json.loads((run / "campaign.json").read_text())
    assert receipt["status"] == "complete" and receipt["total_units"] == 1
    assert receipt["reconciliation"]["evaluated_configurations"] == 2
    assert pl.read_csv(run / "grid-results.csv").height == 2
    orders = pl.read_parquet(next(run.rglob("*-orders.parquet")))
    assert set(orders["candidate_id"].to_list()) == {c.identity for c in grid}
    assert launch.main(args + ["--resume", str(run)]) == 0
    # Completed outputs are verified, never silently trusted on resume.
    output = next(run.rglob("*-orders.parquet"))
    output.write_bytes(output.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="artifact corrupt"):
        launch.main(args + ["--resume", str(run)])


def test_alignment_preserves_unknown_updates_and_never_reads_future():
    frame = pl.DataFrame({"ticker": ["A"] * 4, "time_us": [1000000, 2000000, 3000000, 5000000],
                          "value": [1.0, None, float("nan"), 9.0]})
    clocks = np.arange(1, 7, dtype=np.int64) * 1000000
    actual = _align(frame, ("A",), clocks, "value", carry=True)[:, 0]
    assert actual[:2].tolist() == [1, 1]  # null join absence is not an update.
    assert np.isnan(actual[2:4]).all()   # explicit NaN is an update.
    assert actual[4:].tolist() == [9, 9]
    stale = _align(frame, ("A",), clocks, "value", carry=True, tolerance=0)[:, 0]
    assert np.isnan(stale[1]) and np.isnan(stale[5])


def test_union_contains_all_macd_and_extrema_dependencies():
    names = {f.name for f in dependencies()}
    assert {f"{field}@{r}ms" for field in ("macd_line", "macd_signal")
            for r in (1000, 5000, 10000, 30000)} <= names
    assert {"high@1000ms", "low@1000ms", "trade_count@1000ms"} <= names


def test_liquidity_query_pins_attempts_and_completed_local_midnight_buckets():
    market = SimpleNamespace(build_id="b", units=[SimpleNamespace(ticker="A", stage="broker_100ms", attempt_id="x")])
    statement = liquidity_sql(market, ("A",), "2026-08-18", 1000000, 2000000, 4000000)
    assert "arte.liquidity_100ms_v1" in statement
    assert "build_id='b'" in statement and "toUUID('x')" in statement
    assert "bucket_index>=10" in statement and "bucket_index<30" in statement
    assert "sum(execution_notional)" in statement
    assert "argMax(cumulative_execution_notional" in statement
    assert "quote_timestamp_us<=last_event_us" in statement
    assert "last_event_us<1000000+(toInt64(bucket_index)+1)*100000" in statement


def test_export_excludes_padded_lanes(tmp_path):
    c = Candidate("signal", positions=5)
    runner = SqueezeRunner(synthetic_tape(listings=1), [c, c])
    runner.run()
    path = tmp_path / "fills.parquet"
    export_ledger(runner, [c.identity], path)
    frame = pl.read_parquet(path)
    assert frame.height == int(runner.fill_count[0])
    assert frame["candidate_id"].unique().to_list() == [c.identity]
    orders = tmp_path / "orders.parquet"
    export_orders(runner, [c.identity], orders)
    table = pl.read_parquet(orders)
    assert table.height == 5
    assert table["requested_quantity"].sum() == table["buy_filled"].sum() + table["cancelled_quantity"].sum()


def test_terminal_residual_is_invalid_not_synthetic_liquidation():
    tape = synthetic_tape(listings=1)
    tape.volume[79:] = 0
    tape.notional[79:] = 0
    runner = SqueezeRunner(tape, [Candidate("signal", positions=5)],
                            replace(Settings(), target_step_fraction=.25))
    result = runner.run()
    assert result["open_quantity"][0] > 0
    assert result["terminal_valid"].tolist() == [False]
    assert torch.isnan(result["objective"][0])


def test_adaptive_trail_uses_post_entry_window_and_never_loosens():
    prices = [10.0] * 9 + [10.05 + i * .01 for i in range(31)]
    tape = synthetic_tape(prices)
    runner = SqueezeRunner(tape, [Candidate("signal", positions=5, trailing="adaptive")],
                            replace(Settings(), target_step_fraction=.25))
    runner.run(steps=18)
    original = runner.initial_stop.clone()
    assert torch.equal(original, runner.stop)
    runner.run(reset=False, steps=1)
    assert bool((runner.stop[0, 0, :5] > original[0, 0, :5]).all())
    protected = runner.stop.clone()
    tape.close[19:] = 10.1
    tape.high[19:] = 10.12
    tape.low[19:] = 10.08
    runner.run(reset=False, steps=1)
    assert bool((runner.stop >= protected).all())


def test_runtime_refuses_repository_or_alternate_drive():
    with pytest.raises(ValueError, match="no alternate root"):
        require_runtime(Path(__file__).parent / "output")
