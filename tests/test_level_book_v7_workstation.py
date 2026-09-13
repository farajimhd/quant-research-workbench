from datetime import datetime, timezone
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from research.level_book.v7 import workstation as w


def test_budget_reserves_resources_and_avoids_nested_oversubscription():
    budget = w.resource_budget(128, 192 * w.GIB)
    assert budget['workers'] == 56
    assert budget['query_threads'] == 56
    assert budget['admitted_memory_gib'] == 112
    assert w.resource_budget(256, 512 * w.GIB)['workers'] == 60
    assert w.resource_budget(128, 32 * w.GIB)['workers'] == 12
    assert w.resource_budget(128, 192 * w.GIB, threads=2)['workers'] == 37
    for workers in (0, -1, 61):
        with pytest.raises(ValueError):
            w.resource_budget(256, 512 * w.GIB, workers)
    with pytest.raises(ValueError):
        w.resource_budget(128, w.GIB)
    with pytest.raises(ValueError):
        w.resource_budget(128, 192 * w.GIB, threads=4)


@pytest.mark.parametrize('width,height', [(80, 24), (120, 30)])
def test_sixty_worker_display_is_paged_not_scrolling(width, height):
    stamp = datetime.now(timezone.utc).isoformat()
    state = dict(state='running', workers=60, sessions_completed=200, sessions_total=10000,
                 initial_completed=100, started_epoch=1, updated_at=stamp,
                 rows={f'T{i}': dict(state='active', slot=i) for i in range(60)}, worker_progress={})
    output = io.StringIO()
    Console(file=output, width=width, height=height, color_system=None).print(w.render(state, 1, width, height))
    lines = output.getvalue().splitlines()
    assert max(map(len, lines)) <= width
    assert len(lines) <= height
    assert 'page 2/' in output.getvalue()
    assert 'Failed' in output.getvalue() and 'controller age' in output.getvalue()
    assert 'T0 ' not in output.getvalue()


def test_resume_keeps_deferred_and_rechecks_completed_book(tmp_path, monkeypatch):
    plan = dict(plan_hash='p', rows=[dict(ticker='DONE', status='queued', reason=''),
                                   dict(ticker='BAD', status='queued', reason=''),
                                   dict(ticker='AMBIGUOUS', status='deferred', reason='identity')])
    monkeypatch.setattr(w.c, 'load_progress', lambda *a: {'DONE': dict(state='complete', completed=1), 'BAD': dict(state='failed', reason='query')})
    target = tmp_path / 'tickers/DONE'
    w.c.write(target / 'ready.json', dict(plan_hash='p', checkpoint_hash='hash', book_session='2026-08-21'))
    monkeypatch.setattr(w.c, 'verified_book', lambda _: dict(checkpoint_hash='hash'))
    rows, _ = w.restore_progress(tmp_path, plan)
    assert [r['state'] for r in rows.values()] == ['complete', 'failed', 'deferred']
    assert w.restore_progress(tmp_path, plan, retry_failed=True)[0]['BAD']['state'] == 'queued'
    monkeypatch.setattr(w.c, 'verified_book', lambda _: dict(checkpoint_hash='corrupt'))
    with pytest.raises(ValueError, match='checkpoint hash mismatch'):
        w.restore_progress(tmp_path, plan)


def test_worker_uses_pinned_calculation_and_records_result(tmp_path, monkeypatch):
    monkeypatch.setattr(w, 'execution_hashes', lambda: {'scheduler': 'hash'})
    monkeypatch.setattr(w.c, 'checked_plan', lambda _: dict(plan_hash='p'))
    manifest = tmp_path / 'execution.json'
    w.c.write(manifest, dict(scheduler_hashes={'scheduler': 'hash'}, plan_hash='p'))
    calls = []
    def worker(args):
        calls.append(args)
        w.c.write(tmp_path / 'tickers/TEST/progress.json', dict(state='complete', completed=4))
    monkeypatch.setattr(w.c, 'worker', worker)
    assert w.execute_ticker(str(tmp_path), 'TEST', 1, str(manifest))['completed'] == 4
    assert calls[0].threads == 1 and calls[0].ticker == 'TEST'
    monkeypatch.setattr(w, 'execution_hashes', lambda: {'scheduler': 'changed'})
    with pytest.raises(ValueError, match='scheduler changed'):
        w.execute_ticker(str(tmp_path), 'TEST', 1, str(manifest))


def test_second_controller_cannot_clear_stop_or_launch_workers(tmp_path):
    (tmp_path / 'STOP').touch()
    with w.c.exclusive(tmp_path / 'controller.lock'):
        with pytest.raises(ValueError, match='controller is active'):
            w.run(tmp_path, dict(rows=[]), dict(workers=1, threads=1))
    assert (tmp_path / 'STOP').exists()


def test_complete_resume_does_not_submit_any_ticker(tmp_path, monkeypatch):
    plan = dict(plan_hash='p', software={}, rows=[dict(ticker='DONE', status='queued', coverage=dict(days=2))])
    monkeypatch.setattr(w, 'restore_progress', lambda *a: ({'DONE': dict(state='complete')}, {'DONE': dict(completed=2)}))
    monkeypatch.setattr(w.c, 'hashes', lambda: {})
    class Pool:
        def __init__(self, **kw):
            assert kw['max_workers'] == 1
        def submit(self, *a):
            raise AssertionError('Completed ticker was resubmitted')
        def shutdown(self, **kw):
            assert kw['wait']
    monkeypatch.setattr(w, 'ProcessPoolExecutor', Pool)
    w.run(tmp_path, plan, dict(workers=1, threads=1))
    status = w.c.read(tmp_path / 'status.json')
    assert status['state'] == 'complete' and status['sessions_completed'] == 2


def test_low_memory_stops_before_dispatch_and_keeps_queue(tmp_path, monkeypatch):
    plan = dict(plan_hash='p', software={}, rows=[dict(ticker='NEXT', status='queued', coverage=dict(days=2))])
    monkeypatch.setattr(w, 'restore_progress', lambda *a: ({'NEXT': dict(state='queued')}, {}))
    monkeypatch.setattr(w.c, 'hashes', lambda: {})
    monkeypatch.setattr(w.psutil, 'virtual_memory', lambda: SimpleNamespace(available=w.GIB))
    class Pool:
        def __init__(self, **kw):
            pass
        def submit(self, *a):
            raise AssertionError('Dispatched work despite low RAM')
        def shutdown(self, **kw):
            pass
    monkeypatch.setattr(w, 'ProcessPoolExecutor', Pool)
    w.run(tmp_path, plan, dict(workers=1, threads=1))
    status = w.c.read(tmp_path / 'status.json')
    assert status['state'] == 'interrupted'
    assert status['rows']['NEXT']['state'] == 'queued'
    assert 'RAM' in status['stop_reason']


def test_pool_failure_publishes_terminal_status_and_preserves_queue(tmp_path, monkeypatch):
    plan = dict(plan_hash='p', software={}, rows=[dict(ticker='NEXT', status='queued', coverage=dict(days=2))])
    monkeypatch.setattr(w, 'restore_progress', lambda *a: ({'NEXT': dict(state='queued')}, {}))
    monkeypatch.setattr(w.c, 'hashes', lambda: {})
    monkeypatch.setattr(w.psutil, 'virtual_memory', lambda: SimpleNamespace(available=64*w.GIB))
    class Pool:
        def __init__(self, **kw):
            pass
        def submit(self, *a):
            raise RuntimeError('spawn failed')
        def shutdown(self, **kw):
            pass
    monkeypatch.setattr(w, 'ProcessPoolExecutor', Pool)
    with pytest.raises(RuntimeError, match='spawn failed'):
        w.run(tmp_path, plan, dict(workers=1, threads=1))
    status = w.c.read(tmp_path / 'status.json')
    assert status['state'] == 'failed' and status['reason'] == 'spawn failed'
    assert status['rows']['NEXT']['state'] == 'queued'
