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
