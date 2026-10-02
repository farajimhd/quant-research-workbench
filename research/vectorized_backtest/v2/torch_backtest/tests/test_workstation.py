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


def test_isolated_compiler_selects_bundled_cc_without_changing_shared_environment(tmp_path, monkeypatch):
    import sys
    import importlib.util
    from research.vectorized_backtest.v2.torch_backtest import runtime
    target = tmp_path / "dependencies" / "triton-3.7.1.post27"
    cc = target / "triton/runtime/tcc/tcc.exe"
    cc.parent.mkdir(parents=True)
    cc.write_bytes(b"synthetic-existence-witness")
    monkeypatch.setattr(runtime, "DEFAULT", tmp_path)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(torch, "__version__", "2.12.0+cu132")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setenv("PYTHONPATH", "")
    runtime.configure_compiler()
    import os
    assert os.environ["CC"] == str(cc) and sys.path[0] == str(target)
    monkeypatch.setenv("CC", "explicit-operator-compiler")
    runtime.configure_compiler()
    assert os.environ["CC"] == "explicit-operator-compiler"


def test_isolated_compiler_rejects_incompatible_pytorch(tmp_path, monkeypatch):
    import importlib.util
    from research.vectorized_backtest.v2.torch_backtest import runtime
    (tmp_path / "dependencies/triton-3.7.1.post27/triton").mkdir(parents=True)
    monkeypatch.setattr(runtime, "DEFAULT", tmp_path)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(torch, "__version__", "2.8.0")
    with pytest.raises(RuntimeError, match="requires PyTorch 2.12"):
        runtime.configure_compiler()


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


def test_preflight_has_stage_units_without_false_grid_completion():
    stream = StringIO()
    console = Console(file=stream, width=100, color_system=None)
    s = Snapshot(mode="preflight", stage="Verify source contents", phase_completed=512,
                 phase_total=6192, phase_unit="tickers", total=4320)
    console.print(render(s, 60, width=100, height=24))
    text = stream.getvalue()
    assert "512/6,192 tickers" in text and "4,320" not in text and "not executing" in text


@pytest.mark.parametrize("width,height", [(110, 24), (70, 16), (60, 10)])
def test_long_database_error_remains_bounded_with_diagnostic_path(width, height):
    stream = StringIO()
    console = Console(file=stream, width=width, height=height, color_system=None)
    s = Snapshot(status="Failed", error="ACCESS_DENIED: " + "required SELECT permission "*100,
                 output="D:/TradingML/runtimes/job")
    console.print(render(s, 60, width=width, height=height))
    assert len(stream.getvalue().splitlines()) <= height
    assert "ACCESS_DENIED" in stream.getvalue() and "error.json" in stream.getvalue()


def test_failure_retains_reason_and_redacts_traceback(tmp_path):
    from research.vectorized_backtest.v2.torch_backtest.progress import preparation_event
    stream = StringIO()
    with pytest.raises(RuntimeError):
        with Progress(tmp_path / "progress.jsonl", console=Console(file=stream)) as ui:
            ui.emit(preparation_event({"stage": "certify", "completed": 512, "total": 6192}))
            raise RuntimeError("ACCESS_DENIED password=private-token http://user:private-token@host")
    error = json.loads((tmp_path / "error.json").read_text())
    assert "ACCESS_DENIED" in error["reason"] and "private-token" not in json.dumps(error)
    assert ui.snapshot.completed == 0 and ui.snapshot.phase_completed == 512


def test_certified_duplicate_ticker_is_named_without_dropping_rows(tmp_path, monkeypatch):
    from datetime import date
    from research.vectorized_backtest.v2.torch_backtest.source import arte_source
    from research.vectorized_backtest.v2.torch_backtest.source.common import digest
    day = date(2026, 9, 18)
    members = [dict(ticker='LGHL', symbol_id='a', listing_id='one', security_id='s', source_run_id='r', inserted_at='t'),
               dict(ticker='LGHL', symbol_id='b', listing_id='two', security_id='s', source_run_id='r', inserted_at='t')]
    certificate = dict(status='certified', revision='preopen-tradable-snapshot-v3',
        captured_at_utc='2026-09-18T07:00:00+00:00', available_at_utc='2026-09-18T07:01:00+00:00',
        cutoff_utc='2026-09-18T08:00:00+00:00', snapshot_id='p', row_count=2, tradable_count=2, source_hash=3)
    source = dict(definition={'plan': {'population': [dict(session_date=str(day), certificate=certificate,
                   snapshot_hash=digest(members))]}}, units={str(day): {'LGHL': {}}})
    monkeypatch.setattr(arte_source, 'query', lambda c, statement:
                        [dict(n=2, tradable=2, source_hash=3)] if 'count()' in statement else members)
    with pytest.raises(ValueError, match='LGHL maps to 2 listing rows'):
        arte_source.population(None, source, day, diagnostic_directory=tmp_path)
    report = json.loads((tmp_path / 'population-identity-error.json').read_text())
    assert report['excluded_rows'] == 0 and len(report['rejected_identity_rows']) == 2
    assert report['planned_tickers'] == 1 and report['snapshot_rows'] == 2
    members.append(dict(ticker='A', symbol_id='c', listing_id='three', security_id='a', source_run_id='r', inserted_at='t'))
    certificate.update(row_count=3, tradable_count=3)
    source['definition']['plan']['population'][0]['snapshot_hash'] = digest(members)
    source['units'][str(day)]['A'] = {}
    def verified_query(c, statement):
        if 'count()' in statement:
            return [dict(n=3, tradable=3, source_hash=3)]
        assert 'AND is_tradable=1' in statement
        return members
    monkeypatch.setattr(arte_source, 'query', verified_query)
    selected, saved = arte_source.population(None, source, day, excluded_tickers=('LGHL',), diagnostic_directory=tmp_path)
    assert [r['ticker'] for r in selected] == ['A']
    assert len(saved['eligibility']['excluded_identity_rows']) == 2
    assert saved['eligibility']['excluded_tickers'] == ['LGHL']
    source['definition']['plan']['population'][0]['snapshot_hash'] = 'tampered'
    with pytest.raises(ValueError, match='Population no longer matches'):
        arte_source.population(None, source, day, excluded_tickers=('LGHL',))


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
    assert all(c[c.index("--exclude-tickers")+1] == "LGHL" for c in calls)
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
