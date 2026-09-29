"""Bounded worker defaults use workstation cores without nested Polars pools."""
import pytest

from research.rl_trading.v6.config import worker_plan


def test_explicit_64_worker_plan_requires_headroom():
    plan = worker_plan(requested=64, available_gib=400, logical_cores=64)
    assert plan.listing_workers == 64
    assert plan.query_threads_each == 1
    assert plan.max_in_flight == 128
    with pytest.raises(ValueError):
        worker_plan(requested=64, available_gib=90, logical_cores=64)
    with pytest.raises(ValueError):
        worker_plan(requested=64, available_gib=400, logical_cores=32)
