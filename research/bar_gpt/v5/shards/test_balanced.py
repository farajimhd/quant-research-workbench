"""Meaningful sampling, cross-session, split and canonical-query parity checks."""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from .balanced_build import Quotas
from .contract import SAMPLE_DTYPE
from .origin_samples import samples, select_origins


def test_twenty_percent_without_replacement():
    a = select_origins(1000, 42, 0.2)
    assert len(a) == 200 and len(np.unique(a)) == 200
    np.testing.assert_array_equal(a, select_origins(1000, 42, 0.2))


def test_sliding_daily_and_cross_session_split():
    values = np.full((100, 21), 10, dtype=np.float32)
    valid = np.ones_like(values, dtype=np.uint8)
    # Daily origins cross both overnight gaps and an actual effective split clock.
    first = np.datetime64("2022-07-01T20:00:00", "us").astype(np.int64)
    ends = first + np.arange(100, dtype=np.int64) * 86400_000_000
    actions = [dict(execution_date="2022-09-01", split_from=20, split_to=1)]
    data, tail = samples(values, valid, ends, 86400_000_000, 42, actions, fraction=1.0)
    assert len(data) == 37 and len(tail[2]) == 63
    assert (data["values"][:, :, :6] > 0).all()
    assert (data["values"][0, :20, 3] == 200).all()
    assert (data["values"][0, :20, 4] == 0.5).all()
    assert (data["values"][0, :, 5] == 10).all()  # Counts are never split-adjusted.
    assert (values == 10).all()  # Source/tail remain in their original share basis.


def test_no_padding_and_no_partial_context():
    v = np.ones((63, 21), dtype=np.float32)
    m = np.ones_like(v, dtype=np.uint8)
    t = np.arange(63, dtype=np.int64) * 100000 + 1_600_000_000_000_000
    data, tail = samples(v, m, t, 100000, 42, [])
    assert len(data) == 0
    data, _ = samples(
        v[:2],
        m[:2],
        t[-1] + np.array([100000, 200000]),
        100000,
        42,
        [],
        fraction=1.0,
        previous=tail,
    )
    assert len(data) == 2 and (data["end_us"] > 0).all()


def test_quota_enforces_saved_liquidity_counts():
    quotas = Quotas(7241 * 10000)
    data = np.zeros(1000, dtype=SAMPLE_DTYPE)
    data["timeframe_us"] = 100000
    low = quotas.allocate(data, 0)
    high = quotas.allocate(data, 1)
    assert len(low) == int(quotas.targets["100000"] * 0.25)
    assert len(high) == min(1000, int(quotas.targets["100000"] * 0.75))
    assert len(quotas.allocate(data, 0)) == 0
    quotas.finish(100000, 0, len(low), True)
    assert quotas.saved["100000"][0] == len(low)


@pytest.mark.skipif(
    os.getenv("BARGPT_LIVE_TESTS") != "1", reason="Read-only canonical ClickHouse probe"
)
def test_range_matches_existing_audited_session_query():
    from .source import client, arrow
    from .sql import day_query, clock
    from .range_sql import range_query

    root = Path(
        r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\bar_gpt\shards-v4-pilot-20261107"
    )
    job = next((root / "jobs").glob("2025-*"))
    bounds = json.loads((job / "index.json").read_text())
    rules = json.loads((root / "contract.json").read_text())["contract"]["authority"][
        "rules"
    ]
    row = next(r for r in bounds if r["source_date"] == "2025-06-10")
    c = client(memory_gib=3)
    try:
        old = arrow(
            c,
            day_query("SLVO", "2025-06-10", bounds, rules, 2, 42, 100),
            "bargpt-old-parity",
        )
        new = arrow(
            c,
            range_query(
                "SLVO", bounds, rules, row["first_ordinal"], row["next_ordinal"]
            ),
            "bargpt-new-parity",
        )
        # The old query samples an offset and pads its tail later. Compare every
        # retained shared clock, not the intentionally different origin policies.
        old_rows = {
            (int(r["g.frame"]), int(r["end_us"])): r
            for r in old.to_dicts()
            if r["g.frame"] in (100000, 1000000, 10000000, 60000000)
        }
        compared = 0
        for r in new.to_dicts():
            baseline = old_rows.get((r["resolution"], r["end_us"]))
            if baseline:
                np.testing.assert_array_equal(r["values"][:18], baseline["values"][:18])
                np.testing.assert_array_equal(r["valid"][:18], baseline["valid"][:18])
                compared += 1
        assert compared > 800
    finally:
        c.close()
