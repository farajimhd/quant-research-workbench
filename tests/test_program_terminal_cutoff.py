"""Closing clocks preserve the native simulator's activation/causality gates."""
from datetime import datetime, timedelta, timezone

import pytest

from src.backend.backtest_program_session import program_terminal_cutoff
from src.trading_runtime.simulated_broker import SimulationConfig


@pytest.mark.parametrize("resolution,delay,lead_seconds", [
    (100, 0, 2), (100, 1000, 2), (100, 1500, 3),
    (1000, 0, 2), (1000, .001, 3), (1000, 1000, 3), (1000, 1500, 4),
])
def test_closing_window_leaves_cancel_clock_and_eligible_final_bar(resolution, delay, lead_seconds):
    end = datetime(2026, 10, 12, 20, tzinfo=timezone.utc)
    config = SimulationConfig(liquidity_resolution_ms=resolution, new_order_activation_delay_ms=delay)
    closing = program_terminal_cutoff(end, config, 1000)
    assert closing == end - timedelta(seconds=lead_seconds)
    # Pending acquisitions may be cancelled at closing; the reduction can be
    # requested at the next programme clock and still precede final-bar start.
    submitted = closing + timedelta(seconds=1)
    bar_start = end - timedelta(milliseconds=resolution)
    assert submitted <= bar_start
    activation_clock = bar_start if resolution == 1000 else end
    assert submitted + timedelta(milliseconds=delay) <= activation_clock
    # The next later programme clock cannot reserve that same cancel/exit path.
    later_reduction = submitted + timedelta(seconds=1)
    assert (later_reduction > bar_start
        or later_reduction + timedelta(milliseconds=delay) > activation_clock)


def test_closing_window_rejects_unbound_programme_clock():
    end = datetime(2026, 10, 12, 20, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="native clock"):
        program_terminal_cutoff(end.replace(tzinfo=None), SimulationConfig(), 1000)
    with pytest.raises(ValueError, match="native clock"):
        program_terminal_cutoff(end, SimulationConfig(), 100)
