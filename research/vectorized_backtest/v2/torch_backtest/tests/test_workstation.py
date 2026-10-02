"""Resource, calendar, terminal and launch controls; no historical experiments."""
from io import StringIO
import json
from types import SimpleNamespace

import pytest
from rich.console import Console
import torch

from research.vectorized_backtest.v2.torch_backtest.availability import select_dates, session_bounds
from research.vectorized_backtest.v2.torch_backtest.gpu import memory_plan
from research.vectorized_backtest.v2.torch_backtest.progress import Progress, Snapshot, render
from research.vectorized_backtest.v2.torch_backtest.encoding.clickhouse import admission_sql
from research.vectorized_backtest.v2.torch_backtest.encoding.config import Funnel


def test_calendar_selects_all_36_dates_and_rejects_missing_trading_day():
    import pandas_market_calendars as mcal
    days = [d.date().isoformat() for d in mcal.get_calendar("XNYS").schedule(
        start_date="2026-07-30", end_date="2026-09-18").index]
    rows = [{"day": d} for d in days]
    selected, closed = select_dates(rows)
    assert len(selected) == 36 and "2026-09-07" in closed
    assert select_dates(rows, preflight=True)[0] == [{"day": "2026-09-18"}]
    assert select_dates(rows, single="2026-08-18")[0] == [{"day": "2026-08-18"}]
    with pytest.raises(ValueError, match="Missing expected"):
        select_dates([r for r in rows if r["day"] != "2026-08-05"])
    with pytest.raises(ValueError, match="not in"):
        select_dates(rows, single="2026-09-07")


def test_sessions_and_early_close_follow_exchange_calendar():
    assert session_bounds("2026-09-18", "premarket") == ("04:00", "09:30")
    assert session_bounds("2026-09-18", "regular") == ("09:30", "16:00")
    assert session_bounds("2026-09-18", "afterhours") == ("16:00", "20:00")
    assert session_bounds("2026-11-27", "regular") == ("09:30", "13:00")


def test_gpu_capacity_headroom_is_bounded_not_a_vram_target():
    p = memory_plan(94*1024**3, 96*1024**3, 12*1024**3, 1500)
    assert p["choices"][-1] == 1024 and p["reserve_gib"] >= 14
    with pytest.raises(MemoryError):
        memory_plan(1*1024**3, 96*1024**3, 12*1024**3, 1500)


@pytest.mark.parametrize("width,height", [(110, 24), (70, 16), (60, 10)])
@pytest.mark.parametrize("status", ["Running", "Complete", "Failed", "Interrupted"])
def test_terminal_layout_is_bounded_and_failures_remain_visible(width, height, status):
    stream = StringIO()
    console = Console(file=stream, width=width, height=height, color_system=None)
    s = Snapshot(status=status, stage="Saving verified batch", focus="2026-09-18 · premarket 04:00–09:30 New York",
        completed=3072, total=155520, batch=1024, listings=1500,
        message="Saved batches retained; resume using the exact job directory")
    console.print(render(s, 123, width=width, height=height))
    lines = stream.getvalue().splitlines()
    assert len(lines) <= height and all(len(line) <= width for line in lines)
    assert status in stream.getvalue() and "Saved" in stream.getvalue()


def test_redirected_output_and_interruption_leave_plain_durable_events(tmp_path):
    stream = StringIO()
    console = Console(file=stream, width=100, color_system=None)
    with pytest.raises(KeyboardInterrupt):
        with Progress(tmp_path / "events.jsonl", console=console) as panel:
            panel.emit({"stage": "Replay", "completed": 10, "total": 20})
            raise KeyboardInterrupt()
    assert "\x1b" not in stream.getvalue() and "Interrupted" in stream.getvalue()
    assert json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])["status"] == "Interrupted"


def test_live_panel_restores_cursor_and_keeps_final_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    stream = StringIO()
    console = Console(file=stream, force_terminal=True, legacy_windows=False, width=70, height=16, color_system=None)
    with Progress(tmp_path / "live.jsonl", console=console) as panel:
        panel.emit({"status": "Running", "stage": "Replay", "completed": 10, "total": 20})
        panel.emit({"status": "Complete", "stage": "All saved", "completed": 20})
    assert "Complete" in stream.getvalue() and "\x1b[?25h" in stream.getvalue()


def test_later_window_admission_preserves_daily_episode_history():
    source = {"build_id": "a", "units": {"2026-09-18": {"A": {"bars": {"attempt_id": "b"}}}}}
    text = admission_sql(source, "2026-09-18", ["A"], Funnel(), 72000000000, 57600000000)
    assert "(x.1+1)*100000>=57600000000" in text
    assert "bucket_index>=144000" in text  # Context preserved from 04:00.
    assert "x.1+3000" in text  # Released episode expiry preserved.


def test_workstation_launcher_groups_sources_and_windows_without_starting_grid(tmp_path, monkeypatch):
    from research.vectorized_backtest.v2.torch_backtest import run_workstation as launch
    sources = [{"day": d, "build_id": str(i), "manifest": "producer.json", "ledger": "producer.sqlite", "tickers": 2}
               for i, d in enumerate(("2026-09-17", "2026-09-18"))]
    monkeypatch.setattr(launch, "configure_reader", lambda repo: None)
    monkeypatch.setattr(launch, "discover_sources", lambda: {"sources": sources, "unavailable": []})
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda i: SimpleNamespace(total_memory=96*1024**3))
    monkeypatch.setattr(torch.cuda, "memory_allocated", lambda: 0)
    tape = SimpleNamespace(tickers=("A", "B"), bytes=100)
    tape.to = lambda *a: tape
    monkeypatch.setattr(launch, "prepare_tape", lambda *a, **k: tape)
    monkeypatch.setattr(launch, "calibrate", lambda *a, **k: {"batch": 32, "state_gib": 20})
    calls = []
    def fake(command, **kwargs):
        calls.append(command)
        kwargs["progress"]({"saved_delta": 4320, "valid_delta": 4320, "replay_delta": 1.})
    monkeypatch.setattr(launch.run_grid, "main", fake)
    assert launch.main(["preflight", "--runtime", str(tmp_path / "preflight"), "--plain"]) == 0
    assert not calls
    assert launch.main(["run", "--runtime", str(tmp_path / "run"), "--sessions", "regular", "afterhours", "--plain"]) == 0
    assert len(calls) == 4  # Two source builds × two independently reset windows.
    assert all(c[c.index("--batch")+1] == "32" for c in calls)
    run = next((tmp_path / "run" / "jobs").iterdir())
    assert json.loads((run / "job.json").read_text())["status"] == "complete"


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_1024_lane_compiled_graph_preserves_independent_accounts():
    from research.vectorized_backtest.v2.torch_backtest import Candidate, SqueezeRunner
    from research.vectorized_backtest.v2.torch_backtest.fixtures import synthetic_tape
    from research.vectorized_backtest.v2.torch_backtest.runtime import configure_caches
    configure_caches()
    c = Candidate("signal", positions=15)
    tape = synthetic_tape(seconds=35, listings=1)
    reference = SqueezeRunner(tape, [c], maximum_fills=256).run()
    runner = SqueezeRunner(tape.to("cuda"), [c]*1024, backend="compiled_graph", maximum_fills=256).compile()
    actual = runner.run()
    for key in ("cash", "fees", "realized", "fill_count", "requested_entry_shares", "filled_entry_shares"):
        assert torch.allclose(actual[key].cpu(), reference[key].expand(1024), atol=1e-7, rtol=0)
    assert torch.allclose(runner.ledger.cpu(), runner.ledger[:1].expand(1024, -1, -1).cpu(), atol=1e-7, rtol=0)
