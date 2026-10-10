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


def test_independent_session_paths_keep_separate_units_and_eta(tmp_path):
    now=[100.];ui=CampaignProgress(tmp_path,contract(),console=Console(file=io.StringIO()),clock=lambda:now[0])
    ui.begin_generation(1);days=['2026-07-30','2026-07-31'];ui.begin_sessions(days)
    ui.session_progress(dict(day=days[0],stage='Replay positions',candidate_offset=0,
        rule_tiles=100,total_rule_tiles=100,completed_seconds=50,total_seconds=200,replay_started=100.))
    ui.session_progress(dict(day=days[1],stage='Evaluate rules',rule_tiles=30,total_rule_tiles=120))
    now[0]=110.;status=ui.snapshot()
    assert status['session_paths'][days[0]]['replay_eta']==30.
    assert status['session_paths'][days[1]]['completed_seconds']==0
    for width,height in ((110,38),(80,24)):
        stream=io.StringIO();console=Console(file=stream,width=width,height=height,color_system=None)
        console.print(render(status,width=width,height=height))
        text=stream.getvalue()
        for day in days:
            assert day+' rules' in text
            assert day+' backtest' in text
        for label in ('Generations','Generation sessions','Candidate batches','Campaign ETA','Messages'):
            assert label in text,text
        assert len(text.splitlines())<=height
    ui.session_progress(dict(day=days[0],stage='Evaluate rules',candidate_offset=512,completed_seconds=0,rule_tiles=0))
    ui.session_progress(dict(day=days[0],candidate_offset=0,completed_seconds=200))
    assert ui.snapshot()['session_paths'][days[0]]['completed_seconds']==0


def test_fixed_terminal_updates_only_changed_cells_and_restores_cursor():
    from research.vectorized_backtest.v7.fixed_terminal import FixedTerminal,changed_spans
    assert list(changed_spans(['Progress 10/20'],['Progress 11/20']))==[(1,11,'1')]
    stream=io.StringIO();console=Console(file=stream,width=30,height=2,force_terminal=True)
    with FixedTerminal(console) as screen:
        screen.update('Progress 10/20')
        stream.seek(0);stream.truncate()
        screen.update('Progress 11/20')
        assert stream.getvalue()=='\x1b[1;11H1'
        screen.update('Progress 11/20')
        assert stream.getvalue()=='\x1b[1;11H1'
    assert '\x1b[2J' not in stream.getvalue() and '\x1b[2K' not in stream.getvalue()
    assert stream.getvalue().endswith('\x1b[?7h\x1b[?25h\x1b[?1049l')


def test_field_labels_keep_columns_when_values_become_available(tmp_path):
    ui=CampaignProgress(tmp_path,contract(),console=Console(file=io.StringIO()),clock=lambda:100.)
    before=ui.snapshot();after={**before,'stage':'Replay positions','replay_eta':1234.,'generation_eta':5678.,
                              'campaign_eta':98765.,'average_batch_seconds':12.}
    old=render(before)['timing'].renderable.plain.splitlines()
    new=render(after)['timing'].renderable.plain.splitlines()
    for index,label in ((0,'replay ETA'),(0,'generation ETA'),(1,'avg batch')):
        assert old[index].index(label)==new[index].index(label)
    assert render(before)['header'].renderable.plain.index('updated')==render(after)['header'].renderable.plain.index('updated')
