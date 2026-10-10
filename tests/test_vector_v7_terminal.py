import io
import json
from pathlib import Path
from rich.console import Console
from research.vectorized_backtest.v7.terminal import CampaignProgress,render


def contract():
    return dict(population=1024,generations=50,batch_size=512,session_workers=2,
                input_receipts={str(i):'receipt' for i in range(30)},execution={})


def test_progress_units_eta_reset_and_terminal_states(tmp_path):
    now=[100.];ui=CampaignProgress(tmp_path,contract(),console=Console(file=io.StringIO()),clock=lambda:now[0])
    ui.begin_generation(1);ui.begin_batch(['a','b'],0,200)
    assert ui.snapshot()['campaign_eta'] is None
    now[0]=110;ui.begin_replay();now[0]=120;ui.replay(100,200)
    assert ui.snapshot()['replay_eta']==10
    now[0]=130;ui.complete_batch(2,30)
    snapshot=ui.snapshot()
    assert snapshot['completed_batches']==2 and snapshot['total_batches']==60
    assert snapshot['generation_eta']==870 and snapshot['campaign_eta']==44970
    ui.update(completed_sessions=2);ui.begin_batch(['c','d'],512,180)
    assert ui.snapshot()['progress']['completed_seconds']==0
    assert ui.snapshot()['replay_eta'] is None
    ui.begin_generation(2)
    assert ui.snapshot()['completed_sessions']==ui.snapshot()['completed_batches']==0
    ui.update(status='complete',completed_generations=50,finished_epoch=140)
    assert ui.snapshot()['campaign_eta']==0
    ui.publish();assert json.loads((tmp_path/'status.json').read_text())['completed_generations']==50


def test_v6_layout_normal_compact_no_color_and_stale_failure(tmp_path):
    ui=CampaignProgress(tmp_path,contract(),console=Console(file=io.StringIO()),clock=lambda:100.)
    ui.begin_generation(1);ui.begin_batch(['2026-07-30','2026-07-31'],0,19799)
    for width,height in ((180,55),(110,38),(80,24)):
        for state in ('running','complete','interrupted','failed'):
            status=ui.snapshot();status.update(status=state,error='Synthetic failure' if state=='failed' else None)
            stream=io.StringIO();console=Console(file=stream,width=width,height=height,color_system=None)
            console.print(render(status,width=width,height=height,now=150.))
            text=stream.getvalue()
            for label in ('V7','Generations','Generation sessions','Candidate batches','Campaign ETA','Messages'):
                assert label in text,(width,height,label,text)
            assert '\x1b' not in text
            assert len(text.splitlines())<=height
            if state=='failed':assert 'Synthetic failure' in text


def test_failure_receipt_and_plain_output(tmp_path):
    import pytest
    stream=io.StringIO()
    with pytest.raises(ValueError,match='broken input'):
        with CampaignProgress(tmp_path,contract(),console=Console(file=stream)) as ui:
            ui.begin_generation(1);raise ValueError('broken input')
    status=json.loads((tmp_path/'status.json').read_text())
    assert status['status']=='failed' and status['campaign_eta'] is None
    assert '\x1b' not in stream.getvalue()
    assert not ui.thread.is_alive()
