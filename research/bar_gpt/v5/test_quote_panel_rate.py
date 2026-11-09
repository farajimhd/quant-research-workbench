"""A rate must not blend training/validation admission or resumed counters."""

from rich.console import Console

from .quote_repair_panel_v1 import RecentRate, render


def test_rate_resets_when_admission_or_receipt_counter_changes():
    rate = RecentRate()
    item = dict(
        root="base", state="repairing", admission_lanes=32, updated=100, completed=1000
    )
    assert rate.update(item) is None
    assert rate.update(dict(item, updated=120, completed=1400)) == (20, 20)
    assert (
        rate.update(dict(item, admission_lanes=4, updated=130, completed=1410)) is None
    )
    assert rate.update(dict(item, admission_lanes=4, updated=150, completed=1450)) == (
        2,
        20,
    )
    assert (
        rate.update(dict(item, admission_lanes=4, updated=160, completed=1200)) is None
    )


def test_compact_redirected_panel_exposes_validation_admission():
    import io
    import time

    output = io.StringIO()
    console = Console(file=output, width=80, force_terminal=False)
    console.print(
        render(
            dict(
                state="running",
                updated=time.time(),
                recent_rate=(2.5, 60),
                datasets=[
                    dict(
                        name="phase1-base",
                        state="repairing",
                        completed=350000,
                        total=567835,
                        active=3,
                        admission_lanes=4,
                        training_guard="trainer validation (running)",
                    )
                ],
            ),
            80,
        )
    )
    text = output.getvalue()
    assert "3/4 active/admitted" in text and "2.5 receipts/s" in text
    assert "validation" in text and "350,000/567,835" in text
    assert "\x1b" not in text
