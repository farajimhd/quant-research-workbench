from datetime import date
import json
from pathlib import Path
from threading import Event,Barrier,Lock
from types import SimpleNamespace

import pytest

from pipelines.market_sip.flatfiles.repair_progress import Prefetch,Progress,render
from scripts.run_market_data_pipeline import arguments,classify


def test_historical_dates_and_forwardfill_fail_closed():
    a=arguments(['backfill','--start-date','2025-01-02','--run-dir','D:/TradingML/runtimes/example'])
    assert a.start_date==date(2025,1,2) and a.copy_workers==2
    with pytest.raises(SystemExit):
        arguments(['forwardfill','--run-dir','D:/TradingML/runtimes/example'])


def test_classification_reuses_valid_receipts(monkeypatch):
    from scripts import run_market_data_pipeline as launcher
    inv=dict(missing=(),incomplete=(),source_hashes={'2026-01-02':'a'})
    proof=dict(verified={'2026-01-02':{'evidence_hash':'b'}},missing=())
    monkeypatch.setattr(launcher.source,'inventory',lambda *a:inv)
    monkeypatch.setattr(launcher.source,'reporting_inventory',lambda *a:proof)
    assert classify(None,date(2026,1,1),date(2026,1,2))==(inv,proof)
    inv['missing']=(date(2026,1,2),)
    with pytest.raises(ValueError,match='cannot repair missing'):
        classify(None,date(2026,1,1),date(2026,1,2))


def test_concurrent_copy_and_verified_reuse(tmp_path,monkeypatch):
    from pipelines.market_sip.flatfiles import archive_staging
    archive=tmp_path/'g';cache=tmp_path/'d';runtime=tmp_path/'run'
    archive.mkdir();cache.mkdir();runtime.mkdir()
    paths=SimpleNamespace(archive=archive,cache=cache)
    files=[]
    for i in range(2):
        key=Path(f'{i}.csv.gz');payload=bytes([i])*10000
        (archive/key).write_bytes(payload)
        files.append((date(2026,1,i+2),dict(key=key,source=archive/key,bytes=len(payload))))
    original=archive_staging.copy_file;barrier=Barrier(2);seen=[];lock=Lock()
    def copying(*a,**kw):
        barrier.wait(timeout=5)
        with lock:seen.append(a[0])
        return original(*a,**kw)
    monkeypatch.setattr(archive_staging,'copy_file',copying)
    with Prefetch(files,paths,Event(),Progress(runtime),reserve_bytes=0,reserve_fraction=0,max_bytes=20000) as pool:
        receipts=[pool.get(day) for day,_ in files]
    assert len(seen)==2 and all(r['status']=='staged' and len(r['sha256'])==64 for r in receipts)
    monkeypatch.setattr(archive_staging,'copy_file',original)
    with Prefetch(files,paths,Event(),Progress(runtime),reserve_bytes=0,reserve_fraction=0,max_bytes=20000) as pool:
        assert [pool.get(day)['sha256'] for day,_ in files]==[r['sha256'] for r in receipts]


def test_cached_corruption_fails(tmp_path):
    archive=tmp_path/'g';cache=tmp_path/'d';runtime=tmp_path/'run'
    archive.mkdir();(cache/'_retained_reporting').mkdir(parents=True);runtime.mkdir()
    (archive/'x').write_bytes(b'good');(cache/'_retained_reporting'/'x').write_bytes(b'evil')
    files=[(date(2026,1,2),dict(key=Path('x'),source=archive/'x',bytes=4))]
    with pytest.raises(ValueError,match='differs'):
        with Prefetch(files,SimpleNamespace(archive=archive,cache=cache),Event(),Progress(runtime),
                      reserve_bytes=0,reserve_fraction=0,max_bytes=4) as pool:
            pool.get(files[0][0])


def test_stop_drains_threads(tmp_path):
    runtime=tmp_path/'run';runtime.mkdir();stop=Event();stop.set()
    with Prefetch([],SimpleNamespace(cache=tmp_path,archive=tmp_path),stop,Progress(runtime)) as pool:
        assert not pool.pending
    assert all(not thread.is_alive() for thread in pool.pool._threads)


def test_progress_snapshot_and_compact_render(tmp_path):
    from rich.console import Console
    from io import StringIO
    p=Progress(tmp_path);p.update(status='running',total=3,reused=1,queued=2)
    p.worker('COPY-1','Copy G → D',day='2026-04-01',done=5,total=10)
    p.worker('SQL-1','Waiting mutation',detail='2 parts remaining')
    state=json.loads((tmp_path/'status.json').read_text())
    for width,height in ((80,24),(120,35),(60,15)):
        stream=StringIO();console=Console(file=stream,width=width,height=height,color_system=None)
        console.print(render(state,width,height))
        text=stream.getvalue()
        assert 'COPY-1' in text and 'SQL-1' in text and '50%' in text
        assert '\x1b' not in text


def test_prefetch_budget_rejects_oversized_file(tmp_path):
    with pytest.raises(ValueError,match='exceeds'):
        Prefetch([(date(2026,1,2),dict(bytes=100))],None,Event(),None,max_bytes=99)


def test_two_sql_workers_prepare_independent_days_and_drain(tmp_path,monkeypatch):
    from pipelines.market_sip.flatfiles import repair_preparation as prep
    days=(date(2026,4,1),date(2026,4,2))
    barrier=Barrier(2);clients=[];seen=[]
    class Client:
        def __init__(self):self.closed=False;clients.append(self)
        def query(self,*args,**kwargs):return []
        def close(self):self.closed=True
    def recover(query,paths,inventory,selected,claim,stop,**kwargs):
        assert kwargs['capture_only'] and not kwargs['capture_tape']
        seen.extend(selected);barrier.wait(timeout=5)
        return dict(captured=((str(selected[0]),'hash'),))
    monkeypatch.setattr(prep.source,'inventory',lambda *a,**k:{})
    monkeypatch.setattr(prep.source,'mutations_overlap',lambda *a:False)
    monkeypatch.setattr(prep.retained_reporting,'recover',recover)
    stop=Event()
    with prep.Preparation({(2026,4):days},{},set(),SimpleNamespace(cache=tmp_path),
            SimpleNamespace(check=lambda:None),stop,Progress(tmp_path),Client,SimpleNamespace(get=lambda d:None)) as pool:
        pool.wait((2026,4));pool.release((2026,4))
        assert pool.prepared==2
    assert sorted(seen)==list(days) and len(clients)==2 and all(c.closed for c in clients)
    assert not pool.thread.is_alive()


def test_sql_worker_memory_budget():
    assert arguments(['backfill','--start-date','2026-01-01','--run-dir','x']).sql_workers==2
    with pytest.raises(SystemExit):
        arguments(['backfill','--start-date','2026-01-01','--run-dir','x','--sql-workers','4'])


def test_terminal_stale_failure_and_bounded_logs(tmp_path):
    from pipelines.market_sip.flatfiles.repair_terminal import recent_events
    from rich.console import Console
    from io import StringIO
    path=tmp_path/'events.log'
    path.write_text('2026-01-01T01:02:03+00:00 SQL_0 | captured\n'*50+
                    '2026-01-01T01:02:04+00:00 COPY_0 | bytes (20%)\n')
    events=recent_events(path)
    assert len(events)==1 and events[0]['repeats']==50
    state=dict(status='running',updated_at='2020-01-01T00:00:00+00:00',total=10,reused=2,workers={},recent_events=events)
    stream=StringIO();console=Console(file=stream,width=80,height=24,color_system=None)
    console.print(render(state,80,24));assert 'STALE' in stream.getvalue()
    state.update(status='failed',message='SSD reservation exhausted')
    stream=StringIO();console=Console(file=stream,width=80,height=24,color_system=None)
    console.print(render(state,80,24))
    assert 'FAILED' in stream.getvalue() and 'SSD reservation exhausted' in stream.getvalue()
    assert 'STALE' not in stream.getvalue() and '\x1b' not in stream.getvalue()
