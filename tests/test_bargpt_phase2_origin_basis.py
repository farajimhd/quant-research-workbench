"""One causal origin governs all timeframe views and stock-split adjustments."""

import numpy as np
import pytest

from research.bar_gpt.v5.phase2_shards.contract import (
    BAR,
    BLOCK,
    FRAMES,
    Context,
    encoder_window,
)
from research.bar_gpt.v5.shards.phase1_contract import pack
from research.bar_gpt.v5.shards.sql import clock
from research.bar_gpt.v5.shards.storage import adjust_splits

PRICES = [0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15]
SIZES = [4, 10, 16]
COUNTS = [5, 11, 17]
ACTIONS = [
    dict(execution_date="2025-06-03", split_from=1, split_to=2),
    dict(execution_date="2025-06-04", split_from=10, split_to=1),
]


def historical_window():
    """Synthetic daily window wholly before both corporate actions."""
    bars = np.zeros(64, dtype=BAR)
    bars["values"][:, PRICES] = 100
    bars["values"][:, SIZES] = 200
    bars["values"][:, COUNTS] = 7
    bars["valid"] = 1
    bars["end_us"] = clock("2025-06-02", 16) - np.arange(63, -1, -1) * FRAMES[4]
    return bars


def test_older_daily_view_uses_current_origin_and_compounds_only_effective_splits():
    bars = historical_window()
    unchanged = bars.copy()
    for day, price, size in (
        ("2025-06-02", 100, 200),
        ("2025-06-03", 50, 400),
        ("2025-06-04", 500, 40),
    ):
        sample = encoder_window(bars, FRAMES[4], ACTIONS, origin_us=clock(day, 17))
        np.testing.assert_array_equal(sample["values"][0][:, PRICES], price)
        np.testing.assert_array_equal(sample["values"][0][:, SIZES], size)
        np.testing.assert_array_equal(sample["values"][0][:, COUNTS], 7)
        np.testing.assert_array_equal(sample["valid"][0], bars["valid"])
        np.testing.assert_array_equal(sample["end_us"][0], bars["end_us"])
    np.testing.assert_array_equal(bars, unchanged)


def test_cross_session_window_adjusts_only_pre_split_bars():
    bars = historical_window()
    bars["end_us"][:32] = clock("2025-06-02", 20) - np.arange(31, -1, -1) * FRAMES[0]
    bars["end_us"][32:] = clock("2025-06-03", 4) + np.arange(1, 33) * FRAMES[0]
    bars["values"][32:, PRICES] = 50
    bars["values"][32:, SIZES] = 400
    sample = encoder_window(bars, FRAMES[0], ACTIONS, origin_us=int(bars["end_us"][-1]))
    np.testing.assert_array_equal(sample["values"][0][:, PRICES], 50)
    np.testing.assert_array_equal(sample["values"][0][:, SIZES], 400)


def test_phase_one_default_basis_is_unchanged_and_batched_origins_work():
    bars = historical_window()
    samples = pack(
        np.stack([bars["values"]] * 2),
        np.stack([bars["valid"]] * 2),
        np.stack([bars["end_us"]] * 2),
        FRAMES[4],
        0,
    )
    legacy = samples.copy()
    adjust_splits(legacy, ACTIONS)
    np.testing.assert_array_equal(legacy, samples)
    adjust_splits(
        samples,
        ACTIONS,
        origin_us=np.array([clock("2025-06-02", 17), clock("2025-06-03", 10)]),
    )
    np.testing.assert_array_equal(samples["values"][0][:, PRICES], 100)
    np.testing.assert_array_equal(samples["values"][1][:, PRICES], 50)


def test_future_bar_is_rejected_even_without_corporate_actions():
    bars = historical_window()
    with pytest.raises(ValueError, match="cannot precede"):
        encoder_window(bars, FRAMES[4], [], origin_us=int(bars["end_us"][-1] - 1))


def test_daily_availability_tracks_completed_regular_session_close():
    arrays = [np.empty(0, dtype=BAR) for _ in FRAMES]
    arrays[0] = np.zeros(2, dtype=BAR)
    arrays[0]["end_us"] = [clock("2025-06-03", 10), clock("2025-06-03", 16)]
    arrays[4] = np.zeros(2, dtype=BAR)
    arrays[4]["end_us"] = [clock("2025-06-02", 16), clock("2025-06-03", 16)]
    context = Context()
    context.append(arrays)
    np.testing.assert_array_equal(context.pending["latest"][:, 4], [0, 1])


def test_every_decision_origin_is_exactly_its_last_one_second_bar_close():
    arrays = [np.empty(0, dtype=BAR) for _ in FRAMES]
    arrays[0] = np.zeros(BLOCK, dtype=BAR)
    arrays[0]["end_us"] = clock("2025-06-03", 10) + np.arange(1, BLOCK + 1) * FRAMES[0]
    arrays[1] = np.zeros(26, dtype=BAR)
    arrays[1]["end_us"] = clock("2025-06-03", 10) + np.arange(1, 27) * FRAMES[1]
    blocks, _ = Context().append(arrays)
    np.testing.assert_array_equal(blocks["end_us"][0], arrays[0]["end_us"])
    np.testing.assert_array_equal(blocks["latest"][0, :, 0], np.arange(BLOCK))
    latest = blocks["latest"][0, :, 1]
    # The 10s bar becomes visible exactly at its close.
    assert latest[8] == -1 and latest[9] == 0
    available = latest >= 0
    assert (
        arrays[1]["end_us"][latest[available]] <= blocks["end_us"][0, available]
    ).all()
