"""The workstation verifier is read-only and reports scoped timings."""
from scripts.clickhouse import verify_strategy_one_inputs as command


def test_workstation_one_shot_prints_counts_without_claiming_backtest(monkeypatch, capsys):
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(command, "verify", lambda **_kwargs: {
        "population": 6100, "candidate_tickers": 957,
        "candidate_boundaries": 62072, "pivot_intervals": 100_000,
        "activations": 1234, "source_seconds": 1.0,
        "candidate_seconds": 2.0, "pivot_seconds": 3.0,
        "activation_seconds": 4.0, "candidate_token": "a" * 64,
        "pivot_token": "b" * 64, "activation_token": "c" * 64,
        "price_seconds": 5.0, "price_units": 957,
        "seed_seconds": 6.0, "seed_units": 957,
        "hod_seconds": 7.0, "hod_tickers": 957,
        "entry_seconds": 8.0, "entry_tickers": 957,
        "entry_candidates": 62072, "entry_token": "d" * 64,
        "through_boundary_ms": _kwargs["through_boundary_ms"],
        "visible_candidate_tickers": 957,
        "visible_candidate_boundaries": 62072,
        "visible_activations": 1234, "projection_seconds": .01,
        "sparse_tape_open_seconds": .5,
        "sparse_tape_total_seconds": .7,
        "sparse_tape_boundaries": 1234,
        "static_gate_seconds": .02,
        "static_gate_eligible": 12,
        "static_gate_reject_gap": 3,
        "static_gate_reject_bos": 4,
        "static_gate_reject_support": 5,
        "static_gate_reject_protection": 6,
    })
    assert command.main(["--session-date", "2026-08-18"]) == 0
    output = capsys.readouterr().out
    assert "6100 tradable tickers" in output
    assert "pivot seal 3.000s" in output
    assert "entry seal 8.000s / 62072 candidates" in output
    assert "projection through 20:00 NY" in output
    assert "no Backtest was run or data written" in output
    assert command.main(["--session-date", "2026-08-18",
                         "--through-boundary-ms", "19800000",
                         "--profile-sparse-tape",
                         "--profile-static-gate"]) == 0
    profiled = capsys.readouterr().out
    assert "through 09:30 NY" in profiled
    assert "Sparse tape: 1234 completed boundaries" in profiled
    assert "no orders or fills were simulated" in profiled
    assert "Static entry gate: 12 / 62072 candidates survive" in profiled
    assert "overlapping reasons, no orders simulated" in profiled
