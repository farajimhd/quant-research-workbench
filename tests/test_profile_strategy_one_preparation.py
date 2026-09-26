"""The read-only Strategy 1 profiler reports completed work, not a run result."""
import numpy as np

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


def test_certified_read_reports_persisted_path_without_regeneration(monkeypatch, capsys):
    def never_regenerate(*args, **kwargs):
        raise AssertionError("certified read must not regenerate candidates")
    monkeypatch.setattr(cli, "profile", never_regenerate)
    monkeypatch.setattr(cli, "profile_certified", lambda *args, **kwargs:
        cli.CertifiedReadProfile(6100, 957, 62072, 1000, 5000,
                                 3.0, 4.0, 5.0, 6.0))
    assert cli.main(["--build-id", BUILD, "--date", "2026-08-18",
                     "--certified-read"]) == 0
    output = capsys.readouterr().out
    assert "certified read" in output
    assert "candidate boundaries 62072" in output
    assert "projected market stream 6.000s" in output


def test_sparse_profile_reports_exact_candidate_market_rows(monkeypatch, capsys):
    monkeypatch.setattr(cli, "profile_sparse", lambda *args, **kwargs:
        cli.SparseReadProfile(6100, 957, 62072, 62072, 160,
                              3.0, 4.0, 5.0, 6.0))
    assert cli.main(["--build-id", BUILD, "--date", "2026-08-18",
                     "--sparse-market"]) == 0
    output = capsys.readouterr().out
    assert "candidate boundaries 62072 | market rows 62072" in output
    assert "sparse market read 6.000s" in output


def test_sparse_profile_shards_bound_tickers_and_candidate_keys():
    class Item:
        def __init__(self, ticker, count):
            self.ticker = ticker
            self.boundary_ms = np.arange(100, 100 * (count + 1), 100)

    shards = cli._candidate_shards((Item("AAA", 520), Item("BBB", 20)))
    assert [sum(map(len, shard.values())) for shard in shards] == [512, 28]
    assert all(len(shard) <= 8 for shard in shards)
    assert shards[0]["AAA"][0] == 100
    assert shards[1]["AAA"][-1] == 52_000
