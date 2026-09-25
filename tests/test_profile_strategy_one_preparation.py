"""The read-only Strategy 1 profiler reports completed work, not a run result."""
from scripts.clickhouse import profile_strategy_one_preparation as cli
from src.backend.fixed_bar_signal import validate_stream


BUILD = "a" * 64


def test_profiler_rule_contract_and_plain_result(monkeypatch, capsys):
    validate_stream(*cli._rules())
    calls = []
    def fake_profile(*args, **kwargs):
        calls.append((args, kwargs))
        return (6100, 124754, 5302, 957, 14891, 43.132, 17.740, 74.268)
    monkeypatch.setattr(cli, "profile", fake_profile)
    assert cli.main(["--build-id", BUILD, "--date", "2026-08-18"]) == 0
    output = capsys.readouterr().out
    assert "6100 tickers" in output
    assert "candidate boundaries 14891" in output
    assert "Preflight 43.132s" in output
    assert calls[0][1] == {"through_boundary_ms": 57_600_000,
                           "max_workers": 4}
