"""Holding/activity constraints, cross-date graph reuse and operator witnesses."""

from dataclasses import replace
from io import StringIO

import numpy as np
import pytest
import torch
from rich.console import Console

from research.vectorized_backtest.v3.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v3.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v3.torch_backtest.grid import Candidate, Settings
from research.vectorized_backtest.v3.torch_backtest.optimization_ui import (
    SearchPanel,
    render_search,
)
from research.vectorized_backtest.v3.torch_backtest.optimize import (
    constraint_ranks,
    split,
)
from research.vectorized_backtest.v3.torch_backtest.runner import SqueezeRunner
from research.vectorized_backtest.v3.torch_backtest.search_objective import score
from research.vectorized_backtest.v3.torch_backtest.search_runner import SearchRunner
from research.vectorized_backtest.v3.torch_backtest.session_pool import SessionPool


def streaming_tape(seconds=40, listings=2):
    tape = synthetic_tape(seconds=seconds, listings=listings)
    tape.structural_targets = tape.level_lower[None].expand(seconds, -1, -1).clone()
    return tape.validate()


def test_discretionary_exit_waits_three_seconds_but_stop_remains_active():
    tape = synthetic_tape(prices=torch.full((30, 1), 10.0))
    tape.high.fill_(11)
    tape.low.fill_(10)
    tape.fill_price[12].fill_(10.4)
    runner = SqueezeRunner(
        tape,
        [Candidate("signal", positions=1)],
        replace(Settings(), target_step_fraction=0.025),
    )
    runner.run(steps=12)  # First fill 9; target can queue only at 12.
    ledger = runner.ledger[0, : runner.fill_count[0]]
    assert not (ledger[:, 3] == -1).any()
    runner.run(reset=False, steps=1)
    assert runner.ledger[0, 1, 0] == 13
    tape.low.fill_(9)
    stopped = SqueezeRunner(tape, [Candidate("signal", positions=1)])
    stopped.run(steps=11)
    assert stopped.ledger[0, 1, 0] == 11  # age2 protective-stop exception.
    assert stopped.ledger[0, 1, 7] == 2


def test_long_hold_penalty_is_exposure_weighted_and_cash_resets():
    tape = synthetic_tape(prices=torch.full((40, 1), 10.0))
    settings = replace(Settings(), long_hold_seconds=5)
    runner = SqueezeRunner(tape, [Candidate("signal", positions=1)], settings)
    result = runner.run()
    assert result["long_hold_dollar_seconds"].item() > 0
    assert result["filled_batches"].item() == 1
    assert runner.run()["cash"].item() == result["cash"].item()


def test_broker_enforces_discretionary_hold_and_terminal_exception():
    tape = synthetic_tape(prices=torch.full((40, 1), 10.0))
    runner = SqueezeRunner(tape, [Candidate("signal", positions=1)])
    runner.run(steps=9)
    runner.exit_kind.fill_(3)  # Even an early queued rotation cannot fill early.
    runner.run(reset=False, steps=2)
    assert runner.quantity.sum() > 0
    runner.run(reset=False, steps=1)
    assert runner.quantity.sum() == 0
    runner.reset()
    runner.run(steps=9)
    runner.exit_kind.fill_(4)
    runner.run(reset=False, steps=1)
    assert runner.quantity.sum() == 0  # Terminal exits remain exempt.


@pytest.mark.parametrize("backend", ["eager", "compiled_graph"])
def test_unique_ledger_writes_match_atomic_masked_partial_fills_and_checkpoint(
    backend, tmp_path
):
    if backend == "compiled_graph":
        if not torch.cuda.is_available():
            pytest.skip("CUDA unavailable")
        from research.vectorized_backtest.v3.torch_backtest.runtime import (
            configure_caches,
        )

        configure_caches(tmp_path)
    device = "cuda" if backend == "compiled_graph" else "cpu"
    tape = synthetic_tape(seconds=45, listings=7).to(device)
    tape.volume[:, 1] = 10
    tape.quote_valid[:, 2] = False
    tape.admission[3:] += 10
    candidates = [Candidate("signal", positions=p) for p in (1, 5, 10, 15)]
    atomic = SqueezeRunner(
        tape, candidates, backend=backend, ledger_mode="atomic"
    ).compile()
    unique = SqueezeRunner(
        tape, candidates, backend=backend, ledger_mode="unique"
    ).compile()
    a, b = atomic.run(), unique.run()
    assert torch.allclose(atomic.ledger, unique.ledger, rtol=0, atol=1e-7)
    assert torch.allclose(a["cash"], b["cash"], rtol=0, atol=1e-7)
    assert torch.equal(a["fill_count"], b["fill_count"])
    if backend == "eager":
        partial = SqueezeRunner(tape, candidates)
        partial.run(steps=17)
        restored = SqueezeRunner(tape, candidates)
        restored.load_state_dict(partial.state_dict())
        restored.run(reset=False)
        assert torch.equal(restored.ledger, unique.ledger)


def test_activity_counts_batches_not_child_orders_and_every_session():
    a = dict(
        net_pnl=[100, 200],
        drawdown=[10, 10],
        positions_opened=[15, 15],
        filled_batches=[1, 1],
        exposure_seconds=[0, 0],
        terminal_valid=[True, True],
        long_hold_dollar_seconds=[0, 36000000],
    )
    b = dict(a, filled_batches=[0, 1])
    scores, reasons = score([a, b], minimum_training_entries=1, initial_cash=10000)
    assert scores[0] is None and reasons[0] == "minimum_training_activity"
    assert scores[1] is not None
    values, _ = score([dict(a, filled_batches=[1, 30])], minimum_training_entries=1)
    assert values[1] < values[0]  # greater P&L does not hide excess/overdue cost.
    ranks, _ = constraint_ranks([a, b], scores, 1)
    assert ranks[1] > ranks[0]


def test_split_accepts_many_ordered_sessions_and_rejects_leakage():
    def item(day):
        return dict(
            start=f"2026-08-{day:02}T04:00:00-04:00",
            end=f"2026-08-{day:02}T09:30:00-04:00",
        )

    spec = dict(training=[item(18), item(19), item(20)], validation=[item(21)])
    assert len(split(spec)[0]) == 3
    with pytest.raises(ValueError):
        split(dict(training=spec["training"], validation=[item(19)]))


@pytest.mark.parametrize("backend", ["eager", "compiled_graph"])
def test_precomputed_rules_match_inline_all_operations_and_future_prefix(
    backend, tmp_path
):
    if backend == "compiled_graph":
        if not torch.cuda.is_available():
            pytest.skip("CUDA unavailable")
        from research.vectorized_backtest.v3.torch_backtest.runtime import (
            configure_caches,
        )

        configure_caches(tmp_path)
    device = "cuda" if backend == "compiled_graph" else "cpu"
    tape = streaming_tape(42, 3).to(device)
    space = StrategySpace()
    rows = space.sample(np.random.default_rng(93), 8)
    rows[0] = space.default
    thresholds = (10.03, 0.005, 0.001, 100000, 100, 5, 0.1, 10000)
    for lane in range(1, len(rows)):
        rows[lane, space.rules_start : space.connectors_start] = 0
        for j in range(4):
            rows[lane, space.rules_start + j * 6 + 4] = 1
            rows[lane, space.rules_start + j * 6 + 5] = 0.01
        rows[lane, space.rules_start : space.rules_start + 6] = [
            1,
            lane % 4,
            lane,
            lane % 5,
            3,
            thresholds[lane],
        ]
    rows = space.repair(rows)
    inline = SearchRunner(tape, space, rows, backend=backend).compile()
    a = inline.run()
    compiled = SearchRunner(
        tape, space, rows, backend=backend, precompute_rules=True
    ).compile()
    compiled.rule_compiler.prepare()
    # Independent tick-by-tick witness tests the gates themselves, even where
    # a downstream entry condition would mask a gate difference in the ledger.
    witness = SearchRunner(streaming_tape(42, 3), space, rows)
    expected_gates = []
    for slot in range(42):
        expected_gates.append(
            witness._entry_filter(
                witness.tape.clocks[slot], witness.tape.close[slot]
            ).clone()
        )
        witness.run(reset=False, steps=1)
    assert torch.equal(torch.stack(expected_gates), compiled.rule_gate.cpu())
    b = compiled.run()
    assert torch.allclose(inline.ledger, compiled.ledger, rtol=0, atol=1e-7)
    assert torch.allclose(a["net_pnl"], b["net_pnl"], rtol=0, atol=1e-7)
    prior = compiled.rule_gate[:20].clone()
    tape.close[20:] *= 3
    tape.notional[20:] *= 3
    compiled.rule_compiler.prepare()
    assert torch.equal(prior, compiled.rule_gate[:20])


@pytest.mark.parametrize("backend", ["eager", "compiled_graph"])
def test_pool_changes_dates_and_ticker_counts_without_recompiling_or_cash_carry(
    backend, tmp_path
):
    if backend == "compiled_graph":
        if not torch.cuda.is_available():
            pytest.skip("CUDA unavailable")
        from research.vectorized_backtest.v3.torch_backtest.runtime import (
            configure_caches,
        )

        configure_caches(tmp_path)
    space = StrategySpace()
    a, b = (
        synthetic_tape(seconds=40, listings=2),
        synthetic_tape(seconds=40, listings=3),
    )
    offset = 86400
    for name in ("clocks", "admission", "level_from", "level_to"):
        getattr(b, name).add_(offset)
    b.provenance.update(
        start_second=1 + offset, end_second=40 + offset, fingerprint="second-date"
    )
    device = "cuda" if backend == "compiled_graph" else "cpu"
    pool = SessionPool([a, b], space, 2, device=device, backend=backend, resident_gib=0)
    rows = np.tile(space.default, (2, 1))
    pool.evaluate(0, rows)
    actual = pool.evaluate(1, rows)
    runner = next(iter(next(iter(pool.evaluators.values())).runners.values()))
    ptr = runner.tape.close.data_ptr()
    reference = SearchRunner(b, space, rows).run()
    assert np.allclose(
        actual["net_pnl"], reference["net_pnl"].tolist(), atol=1e-7, rtol=0
    )
    assert actual["compile_seconds"] == 0
    pool.evaluate(0, rows)
    assert runner.tape.close.data_ptr() == ptr


@pytest.mark.parametrize("width,height", [(110, 28), (70, 16), (60, 10)])
@pytest.mark.parametrize(
    "status", ["training", "preparing", "failed", "interrupted", "completed"]
)
def test_search_panel_fits_and_preserves_status_and_error(width, height, status):
    stream = StringIO()
    console = Console(file=stream, width=width, height=height, color_system=None)
    state = dict(
        status=status,
        stage="Replay",
        focus="2026-08-18",
        output="D:/TradingML/runtimes/job",
        message="ACCESS_DENIED " * 100,
    )
    console.print(render_search(state, 30, width=width, height=height))
    text = stream.getvalue()
    assert status.upper() in text
    assert len(text.splitlines()) <= height
    assert all(len(line) <= width for line in text.splitlines())
    if status in ("failed", "interrupted"):
        assert "ACCESS_DENIED" in text and "error.json" in text


def test_panel_plain_and_interrupt_are_durable_and_redacted(tmp_path):
    stream = StringIO()
    with pytest.raises(KeyboardInterrupt):
        with SearchPanel(tmp_path, console=Console(file=stream), plain=True) as panel:
            panel.emit(dict(status="training", stage="Replay"))
            raise KeyboardInterrupt("operator cancellation")
    assert "\x1b" not in stream.getvalue()
    assert (tmp_path / "error.json").exists()


def test_monitor_interruption_never_changes_worker_status(tmp_path):
    original = b'{"status":"training"}'
    (tmp_path / "status.json").write_bytes(original)
    with pytest.raises(KeyboardInterrupt):
        with SearchPanel(tmp_path, console=Console(file=StringIO()), read_only=True):
            raise KeyboardInterrupt()
    assert (tmp_path / "status.json").read_bytes() == original
    assert not (tmp_path / "error.json").exists()


def test_prepared_snapshot_is_atomic_identity_bound_and_corruption_fails(tmp_path):
    from research.vectorized_backtest.v3.torch_backtest.prepared_cache import (
        load_prepared,
        save_prepared,
    )

    path = tmp_path / "inputs" / "training_000"
    tape = streaming_tape()
    identity = dict(code_hash="test-only", source="certified-fixture")
    assert load_prepared(path, identity) is None
    save_prepared(path, tape, identity)
    loaded = load_prepared(path, identity)
    assert torch.equal(loaded.close, tape.close)
    with pytest.raises(ValueError, match="identity"):
        load_prepared(path, dict(identity, code_hash="changed"))
    with pytest.raises(ValueError, match="overwrite"):
        save_prepared(path, tape, identity)
    (path / "tape.pt").write_bytes((path / "tape.pt").read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        load_prepared(path, identity)


def test_remote_launcher_streams_script_and_keeps_windows_command_bounded(monkeypatch):
    from research.vectorized_backtest.v3.torch_backtest import launch_remote

    calls = []
    monkeypatch.setattr(
        launch_remote.subprocess,
        "run",
        lambda command, **kw: calls.append((command, kw)),
    )
    assert (
        launch_remote.main(
            [
                "--checkout",
                "D:/TradingML/codes/quant-research-workbench-squeeze-v3-test",
                "--job",
                "D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v3/optimization_jobs/test",
                "--command",
                "profile",
                "--",
                "--population",
                "64",
            ]
        )
        == 0
    )
    command, options = calls[0]
    assert sum(map(len, command)) < 2000
    assert (
        "Register-ScheduledTask" in options["input"]
        and "Interactive" in options["input"]
    )
    assert options["encoding"] == "utf-8"
    assert "visible-dispatch.ps1" in options["input"]


def test_atomic_json_retries_windows_reader_sharing_without_partial_publication(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from research.vectorized_backtest.v3.torch_backtest.runtime import write_json

    original = Path.replace
    calls = []

    def sharing_once(self, target):
        calls.append(self)
        if len(calls) == 1:
            error = PermissionError("reader denies delete-sharing")
            error.winerror = 32
            raise error
        return original(self, target)

    monkeypatch.setattr(Path, "replace", sharing_once)
    write_json(tmp_path / "status.json", dict(status="training"))
    assert len(calls) == 2
    assert (tmp_path / "status.json").read_text().strip().endswith("}")
    assert not list(tmp_path.glob("*.tmp"))
