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
    })
    assert command.main(["--session-date", "2026-08-18"]) == 0
    output = capsys.readouterr().out
    assert "6100 tradable tickers" in output
    assert "pivot seal 3.000s" in output
    assert "no Backtest was run or data written" in output
