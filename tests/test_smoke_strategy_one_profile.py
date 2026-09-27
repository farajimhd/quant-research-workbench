"""The integration probe reports normalized journal work without dumping rows."""
import sys
from types import SimpleNamespace

import pytest

from scripts.clickhouse.smoke_strategy_one_backtest import (
    _SqlCallProfile, _print_completed_profile, _profile_preflight_call,
    _profile_sql_calls,
)


def test_preflight_call_profile_preserves_result_and_reports_bounded_calls(capsys):
    assert _profile_preflight_call(lambda *, value: value + 1, value=2) == 3
    output = capsys.readouterr().out
    assert "Preflight call profile (top 25 cumulative seconds):" in output
    assert "function calls" in output


def test_completed_profile_reports_wall_and_writer_units(capsys):
    controller = SimpleNamespace(
        _stage_timings={"execute": {"calls": 1, "seconds": 12.5,
                                    "maximum_seconds": 12.5}},
        _journal_writer_final_metrics={
            "committed_units": 4, "committed_event_rows": 7,
            "failed_units": 0, "failed": False,
            "queue_depth": 0, "queue_capacity": 8,
            "publish_ns_total": 2_500_000_000,
            "publish_ns_max": 1_000_000_000,
            "publish_by_unit": {
                "TypedJournalBatch": {"units": 3, "event_rows": 6,
                                      "publish_ns_total": 1_500_000_000,
                                      "publish_ns_max": 750_000_000},
                "V4StrategyOneEntryBatch": {"units": 1, "event_rows": 1,
                                            "publish_ns_total": 1_000_000_000,
                                            "publish_ns_max": 1_000_000_000}},
        },
    )
    _print_completed_profile(controller)
    assert capsys.readouterr().out.splitlines() == [
        "Stage execute: calls=1 wall_s=12.500 max_call_s=12.500",
        "Journal writer: committed_units=4 event_rows=7 failed_units=0 "
        "worker_s=2.500 max_unit_s=1.000 queue_capacity=8",
        "  TypedJournalBatch: units=3 event_rows=6 worker_s=1.500 max_s=0.750",
        "  V4StrategyOneEntryBatch: units=1 event_rows=1 worker_s=1.000 max_s=1.000",
    ]


def test_completed_profile_reports_compound_prepare_and_publish(capsys):
    controller = SimpleNamespace(
        _stage_timings={},
        _journal_writer_final_metrics={
            "committed_units": 1, "committed_event_rows": 2,
            "failed_units": 0, "failed": False,
            "queue_depth": 0, "queue_capacity": 8,
            "publish_ns_total": 3_000_000_000,
            "publish_ns_max": 3_000_000_000,
            "compound_prepare_ns_total": 1_000_000_000,
            "compound_publish_ns_total": 2_000_000_000,
            "publish_by_unit": {"V4CompoundBatch": {
                "units": 1, "event_rows": 2,
                "publish_ns_total": 3_000_000_000,
                "publish_ns_max": 3_000_000_000}},
        },
    )
    _print_completed_profile(controller)
    assert "  Compound commit: prepare_s=1.000 publish_s=2.000" in (
        capsys.readouterr().out)


@pytest.mark.parametrize("change", [
    {"failed_units": 1}, {"failed": True}, {"queue_depth": 1},
    {"committed_units": 0},
])
def test_completed_profile_rejects_unsettled_writer(change):
    metrics = dict(committed_units=1, committed_event_rows=1,
                   failed_units=0, failed=False,
                   queue_depth=0, queue_capacity=8,
                   publish_ns_total=1, publish_ns_max=1)
    metrics.update(change)
    with pytest.raises(RuntimeError, match="drained typed journal"):
        _print_completed_profile(SimpleNamespace(
            _stage_timings={}, _journal_writer_final_metrics=metrics))


def test_completed_profile_rejects_unreconciled_family_timings():
    metrics = dict(committed_units=1, committed_event_rows=1,
                   failed_units=0, failed=False,
                   queue_depth=0, queue_capacity=8,
                   publish_ns_total=10, publish_ns_max=10,
                   publish_by_unit={"TypedJournalBatch": {
                       "units": 1, "event_rows": 1, "publish_ns_total": 9,
                       "publish_ns_max": 9}})
    with pytest.raises(RuntimeError, match="do not reconcile"):
        _print_completed_profile(SimpleNamespace(
            _stage_timings={}, _journal_writer_final_metrics=metrics))


def test_sql_profile_keeps_only_bounded_categories(capsys):
    profile = _SqlCallProfile()
    profile.record("SELECT x FROM arte.trading_event_v1", 1.25)
    profile.record("INSERT INTO arte.trading_commit_v4 VALUES (...)", 0.75)
    profile.record("SELECT x FROM arte.bars_v1", 0.5)
    profile.print_summary()
    assert capsys.readouterr().out.splitlines() == [
        "ClickHouse journal_insert: calls=1 client_s=0.750",
        "ClickHouse journal_read: calls=1 client_s=1.250",
        "ClickHouse market_or_control_read: calls=1 client_s=0.500",
        "ClickHouse v7_completed_second_stream: calls=0 iterator_s=0.000",
    ]


def test_sql_profile_restores_client_after_failure(monkeypatch):
    from research.mlops.clickhouse import ClickHouseHttpClient

    def failing(_client, _sql):
        raise RuntimeError("request failed")

    monkeypatch.setattr(ClickHouseHttpClient, "execute", failing)
    profile = _SqlCallProfile()
    with pytest.raises(RuntimeError, match="request failed"):
        with _profile_sql_calls(profile):
            ClickHouseHttpClient.execute(object(),
                                         "SELECT x FROM arte.trading_event_v1")
    assert ClickHouseHttpClient.execute is failing
    assert profile._bins["journal_read"][0] == 1


def test_app_probe_repeats_only_apply_runs_in_one_process(monkeypatch, capsys):
    from scripts.clickhouse import smoke_strategy_one_app_route as probe

    calls = []
    monkeypatch.setattr(probe, "_load_private_credentials",
                        lambda: calls.append("credentials"))
    async def run(*args):
        calls.append(args)
    monkeypatch.setattr(probe, "_run", run)
    monkeypatch.setattr(sys, "argv", ["probe", "--minutes", "1",
                                       "--apply", "--repeat-runs", "2"])
    probe.main()
    assert calls[0] == "credentials"
    assert len(calls) == 3 and calls[1] == calls[2]
    assert capsys.readouterr().out.splitlines() == [
        "App probe 1/2 (cold process)", "App probe 2/2 (warm process)"]
    monkeypatch.setattr(sys, "argv", ["probe", "--repeat-runs", "2"])
    with pytest.raises(SystemExit, match="2"):
        probe.main()
    assert len(calls) == 3
