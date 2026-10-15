"""Status observation cannot block or terminate optimization."""
import json
from io import StringIO
import pytest
from rich.console import Console
from research.vectorized_backtest.v20.progress_io import shared_binary,read_progress
from research.vectorized_backtest.v20.observe_local import snapshot,render
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json


def test_status_read_and_bounded_writer_retry_cooperate(tmp_path):
    import threading,time
    path=tmp_path/'status.json';write_json(path,dict(generation=1));errors=[]
    def publish():
        try:write_json(path,dict(generation=2))
        except BaseException as error:errors.append(error)
    with shared_binary(path) as stream:
        thread=threading.Thread(target=publish);thread.start()
        assert json.load(stream)==dict(generation=1)
        time.sleep(.05)
    thread.join(5)
    assert not thread.is_alive() and not errors
    assert read_progress(path)==dict(generation=2)


def test_permission_error_is_explicit_stale_display(monkeypatch,tmp_path):
    import research.vectorized_backtest.v20.progress_io as module
    def denied(path):raise PermissionError('test sharing conflict')
    monkeypatch.setattr(module,'shared_binary',denied)
    result=read_progress(tmp_path/'status.json',dict(generation=8))
    assert result['generation']==8 and result['progress_stale']
    assert 'PermissionError' in result['progress_read_error']


def test_invalid_progress_json_does_not_become_financial_data(tmp_path):
    path=tmp_path/'status.json';path.write_text('{')
    assert read_progress(path)['progress_stale']


@pytest.mark.parametrize('width,height',[(110,38),(70,24)])
def test_stopped_local_panel_reports_completed_sessions(tmp_path,width,height):
    root=tmp_path;day='2026-07-30';folder=root/'local'/day;folder.mkdir(parents=True)
    write_json(root/'manifest.json',dict(days=[day,'2026-07-31'],local_generations=30))
    write_json(root/'active.json',dict(pid=1,creation_time=0))
    write_json(root/'status.json',dict(status='failed',stage='Permission error reading progress'))
    write_json(folder/'complete.json',dict(status='complete',generations=30))
    write_json(folder/'status.json',dict(status='complete',completed_generations=30))
    write_json(folder/'archive.json',[dict(score=642.)])
    data=snapshot(root);assert data['completed']==1 and not data['running']
    output=StringIO();Console(file=output,width=width,height=height,color_system=None).print(render(data,width,height))
    text=output.getvalue();assert 'OPTIMIZER STOPPED' in text and '1/2' in text and '642.0' in text
    assert '\x1b' not in text
