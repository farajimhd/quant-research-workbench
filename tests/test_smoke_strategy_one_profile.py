"""The integration probe reports normalized journal work without dumping rows."""
from types import SimpleNamespace

import pytest

from scripts.clickhouse.smoke_strategy_one_backtest import _print_completed_profile


def test_completed_profile_reports_wall_and_writer_units(capsys):
    controller = SimpleNamespace(
        _stage_timings={"execute": {"calls": 1, "seconds": 12.5,
                                    "maximum_seconds": 12.5}},
        _journal_writer_final_metrics={
            "committed_units": 4, "failed_units": 0, "failed": False,
            "queue_depth": 0, "queue_capacity": 8,
            "publish_ns_total": 2_500_000_000,
            "publish_ns_max": 1_000_000_000,
        },
    )
    _print_completed_profile(controller)
    assert capsys.readouterr().out.splitlines() == [
        "Stage execute: calls=1 wall_s=12.500 max_call_s=12.500",
        "Journal writer: committed_units=4 failed_units=0 "
        "worker_s=2.500 max_unit_s=1.000 queue_capacity=8",
    ]


@pytest.mark.parametrize("change", [
    {"failed_units": 1}, {"failed": True}, {"queue_depth": 1},
    {"committed_units": 0},
])
def test_completed_profile_rejects_unsettled_writer(change):
    metrics = dict(committed_units=1, failed_units=0, failed=False,
                   queue_depth=0, queue_capacity=8,
                   publish_ns_total=1, publish_ns_max=1)
    metrics.update(change)
    with pytest.raises(RuntimeError, match="drained typed journal"):
        _print_completed_profile(SimpleNamespace(
            _stage_timings={}, _journal_writer_final_metrics=metrics))
