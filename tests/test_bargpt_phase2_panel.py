"""Bounded plain rendering keeps ownership, ETA freshness and errors visible."""

import io
import time

import pytest
from rich.console import Console

from research.bar_gpt.v5.phase2_shards.progress import render


@pytest.mark.parametrize("width,height", [(120, 40), (80, 24)])
def test_panel_reports_process_topology_and_hides_stale_eta(width, height):
    status = dict(
        state="running",
        updated=time.time(),
        bytes=1e12,
        target_bytes=4e12,
        units=10000,
        bytes_per_second=50e6,
        elapsed_seconds=180,
        process_workers=32,
        workers=64,
        io_workers=16,
        blocks=[
            dict(split=split, liquid=liquid, total=100, selected=75)
            for split in range(3)
            for liquid in (0, 1)
        ],
        lanes=[dict(ticker="AAPL", year=2025, stage="processing source range")] * 8,
    )

    def capture():
        stream = io.StringIO()
        console = Console(file=stream, width=width, height=height, force_terminal=False)
        console.print(render(status, width, height))
        return stream.getvalue()

    text = capture()
    assert "32 processes" in text and "16 I/O" in text and "ETA ~17h" in text
    assert "\x1b" not in text and len(text.splitlines()) <= height
    status.update(
        state="failed",
        updated=time.time() - 60,
        errors=[dict(reason="Source identity changed")],
    )
    text = capture()
    assert "ETA unavailable" in text and "Source identity changed" in text
