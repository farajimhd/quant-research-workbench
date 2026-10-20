from io import StringIO
from rich.console import Console
from scripts.monitor_structural_sessions import render,snapshot,main


def test_panel_states_and_compact_bounds():
    for phase in ('waiting_for_bars','plan','profile','backfill','failed','complete'):
        for width,height in ((100,30),(60,18)):
            stream=StringIO();console=Console(file=stream,width=width,height=height,color_system=None)
            console.print(render({'controller':{'phase':phase},'status':{
                'status':phase,'completed':100,'reused':20,'total':200,'failed':0,
                'workers':[{'active':'2026-01-02 AAPL','completed':100,'reused':20}]}} ,width,height))
            text=stream.getvalue()
            assert '100 new' in text and '20 reused' in text
            assert all(len(line)<=width for line in text.splitlines())
            assert '\x1b' not in text


def test_read_only_waiting_entry_point(tmp_path,capsys):
    before=list(tmp_path.iterdir())
    assert snapshot(tmp_path)['status']=={}
    assert main(['--run-dir',str(tmp_path),'--once'])==0
    assert 'waiting for controller' in capsys.readouterr().out
    assert list(tmp_path.iterdir())==before


def test_active_recovery_replaces_old_plan_failure_heading():
    stream=StringIO();console=Console(file=stream,width=100,color_system=None)
    console.print(render({'controller':{'phase':'failed','failed_phase':'plan'},
        'evidence':{'status':'running'},'status':{}}))
    assert 'Waiting for reporting recovery' in stream.getvalue()
    assert 'Structural levels  |  failed' not in stream.getvalue()


def test_dependency_blocks_remain_in_full_population_and_are_not_failures():
    for width,height in ((100,30),(60,18)):
        stream=StringIO();console=Console(file=stream,width=width,height=height,color_system=None)
        console.print(render({'controller':{'phase':'backfill'},'status':{
            'status':'running','completed':10,'reused':5,'total':100,'queued':85,
            'blocked_units':20,'blocked_tickers':2}},width,height))
        text=stream.getvalue()
        assert '15 / 100' in text and '20 ticker-days / 2 tickers' in text
        assert '0 failed' in text
        assert all(len(line)<=width for line in text.splitlines())


def test_new_preflight_does_not_report_previous_owner_failure():
    stream=StringIO();console=Console(file=stream,width=100,color_system=None)
    console.print(render({'controller':{'phase':'profile'},'status':{
        'status':'failed','failed':1,'error':'old AACO failure','total':100},
        'evidence':{'status':'complete'}}))
    text=stream.getvalue()
    assert 'old AACO failure' not in text and '1 failed' not in text
    assert 'Reporting evidence: complete' in text
    assert 'Current verification' not in text


def test_running_panel_shows_all_eight_workers_after_recovery():
    stream=StringIO();console=Console(file=stream,width=110,height=32,color_system=None)
    console.print(render({'controller':{'phase':'backfill'},'status':{
        'status':'running','completed':80,'reused':20,'total':200,'queued':100,
        'workers':[{'active':f'2026-01-02 T{i}','completed':10,'reused':0} for i in range(8)]},
        'evidence':{'status':'complete'}},110,32))
    text=stream.getvalue()
    assert 'Queued ticker-days: 100' in text and 'T7' in text
    assert len(text.splitlines())<=32


def test_reuse_stage_and_per_day_progress_are_visible():
    stream=StringIO();console=Console(file=stream,width=110,height=32,color_system=None)
    console.print(render({'controller':{'phase':'backfill'},'status':{
        'status':'running','completed':0,'reused':31,'total':200,'queued':169,
        'workers':[{'active':'2026-03-11 AAPL','stage':'verifying reuse','completed':0,'reused':31}]}},110,32))
    text=stream.getvalue()
    assert 'verifying reuse' in text and '31 reused' in text
    assert all(len(line)<=110 for line in text.splitlines())


def test_resume_does_not_present_attempt_zero_as_empty_storage():
    stream=StringIO();console=Console(file=stream,width=110,height=32,color_system=None)
    console.print(render({'controller':{'phase':'backfill'},'status':{
        'completed':0,'reused':0,'total':100,'workers':[]},
        'admission':{'persisted_pair_hints':80}},110,32))
    text=stream.getvalue()
    assert 'This attempt verified: 0 / 100' in text
    assert '80 stored pairs (audit pending)' in text
    assert 'unfinished tickers first' in text
