"""Meaningful sampling, cross-session, split and canonical-query parity checks."""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from .balanced_build import Quotas
from .phase1_contract import SAMPLE_DTYPE
from .origin_samples import samples, select_origins


def test_twenty_percent_without_replacement():
    a = select_origins(1000, 42, 0.2)
    assert len(a) == 200 and len(np.unique(a)) == 200
    np.testing.assert_array_equal(a, select_origins(1000, 42, 0.2))


def test_market_only_migration_preserves_all_retained_fields(tmp_path):
    from .contract import SAMPLE_DTYPE as OLD_DTYPE
    from .storage import BAR_DTYPE, Budget, publish
    from .migrate_market_only import migrate_unit

    old = np.zeros(3, dtype=OLD_DTYPE)
    old["values"] = 10
    old["valid"] = 1
    old["values"][..., -3:] = 99  # These columns must not survive migration.
    old["end_us"] = 1_700_000_000_000_000 + np.arange(64) * 100000
    old["timeframe_us"] = 100000
    source = tmp_path / "old"
    target = tmp_path / "new"
    original = publish(
        source,
        old,
        np.empty(0, dtype=BAR_DTYPE),
        dict(
            binding="old",
            identity="old",
            ticker="TEST",
            unit="fine-1-2",
            year=2023,
            liquid=1,
            day="2023-01-01",
        ),
        Budget(10**7),
        1024**2,
    )
    result = migrate_unit(
        source / "receipt.json", target, "new", Budget(10**7), 1024**2
    )
    actual = np.load(target / result["files"][0]["name"], allow_pickle=False)
    assert actual.dtype == SAMPLE_DTYPE and actual["values"].shape == (3, 64, 18)
    np.testing.assert_array_equal(actual["values"], old["values"][..., :18])
    np.testing.assert_array_equal(actual["valid"], old["valid"][..., :18])
    assert result["parent_receipt_hash"] == original["receipt_hash"]


def test_sliding_daily_and_cross_session_split():
    values = np.full((100, 18), 10, dtype=np.float32)
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
    v = np.ones((63, 18), dtype=np.float32)
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


def test_panel_configuration_has_its_own_line_and_fits_native_window():
    """The byte progress bar must not consume or truncate the configuration row."""
    import io
    import time
    from rich.console import Console
    from .progress import render

    quotas = Quotas(10**9).snapshot()
    snapshot = dict(
        state="running",
        updated=time.time(),
        quotas=quotas,
        configuration=dict(
            workers=64,
            io_workers=16,
            chunk_events=500000,
            max_chunk_events=500000,
            coarse_days=20,
        ),
        workers=[dict(id=i + 1, phase="query", done=1, total=10) for i in range(64)],
    )
    stream = io.StringIO()
    Console(file=stream, width=120, height=30, force_terminal=False).print(
        render(snapshot, 120, 30)
    )
    lines = stream.getvalue().splitlines()
    assert len(lines) <= 30 and max(map(len, lines)) <= 120
    assert any(
        line.startswith("Queries 64") and "coarse 20 days" in line for line in lines
    )


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


@pytest.mark.skipif(
    os.getenv("BARGPT_LIVE_TESTS") != "1",
    reason="SELECT-only synthetic ClickHouse check",
)
def test_range_has_no_halt_fields_and_preserves_quote_invalidation():
    from unittest.mock import patch
    from .source import client, arrow, preflight, close
    from .range_sql import range_query
    from .sql import clock

    c = client()
    try:
        rules = preflight(c)["rules"]
        opened = clock("2019-01-03", 4)
        columns = (
            "ticker String,ordinal UInt64,event_meta UInt8,sip_timestamp_us UInt64,"
            "price_primary_int UInt32,price_secondary_int UInt32,size_primary Float32,"
            "size_secondary Float32,condition_token_1 UInt8,condition_token_2 UInt8,"
            "condition_token_3 UInt8,condition_token_4 UInt8,condition_token_5 UInt8"
        )
        events = [
            (0, 0, 1200, 1100),
            (100000, 1, 1150, 0),
            (300000, 0, 0, 0),
            (1100000, 1, 1150, 0),
            (1500000, 0, 0, 0),
            (2100000, 1, 1150, 0),
            (4000000, 0, 1200, 1100),
        ]
        records = []
        for i, (offset, kind, price, bid) in enumerate(events):
            records.append(
                "("
                + ",".join(
                    map(
                        str,
                        [
                            "'TEST'",
                            100 + i,
                            kind,
                            opened + offset,
                            price,
                            bid,
                            10,
                            20,
                            0,
                            0,
                            0,
                            0,
                            0,
                        ],
                    )
                )
                + ")"
            )
        raw = f"SELECT * FROM values('{columns}'," + ",".join(records) + ")"
        timeline = [
            (opened - 1, 99, 0),
            (opened + 300000, 102, 1),
            (opened + 1500000, 104, 0),
        ]
        bounds = [dict(source_date="2019-01-03", first_ordinal=100, next_ordinal=107)]
        with patch.dict(range_query.__globals__, {"event_source": lambda *args: raw}):
            statement = range_query("TEST", bounds, rules, 100, 107)
            result = arrow(c, statement, "bargpt-halt-range-test")
        seconds = result.filter(result["resolution"] == 1000000)
        assert all(len(r) == 18 for r in seconds["values"].to_list())
        assert "states AS" not in statement
        # The zero-price source message invalidates the book; it cannot leave
        # the old positive bid/ask usable at later completed trade bars.
        assert all(r[9] == 0 and r[15] == 0 for r in seconds["valid"].to_list())
    finally:
        close(c)
