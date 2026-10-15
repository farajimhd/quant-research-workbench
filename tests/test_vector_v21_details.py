"""Phase-1 detail projection, session selection, identity and read-only cache."""
import json
from io import StringIO
import pytest
from rich.console import Console
from research.vectorized_backtest.v21 import local_details as d
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json


def fixture(root,day='2026-07-30',generation=30):
    folder=root/'local'/day;g=folder/f'generation-{generation:04d}';(g/day).mkdir(parents=True)
    metrics={k:[0.,0.] for k in d.COLUMNS};metrics.update(net_pnl=[100.,200.],closed_positions=[4,2],winning_positions=[3,1],drawdown=[10.,20.],maximum_position_risk=[50.,80.],maximum_stop_distance=[.03,.04],terminal_valid=[True,True],episode_profits=[[1,2,3,94],[50,150]])
    write_json(g/day/'receipt.json',dict(metrics=metrics,population_sha256='test'));write_json(g/'complete.json',dict(population_sha256='test'))
    write_json(g/'scores.json',dict(ids=[11,12],scores=[80.,70.],eligible=[True,False]))
    payload=dict(rules={s:dict(nodes=[dict(op=1,value=1.,unit='bool',a=-1,b=-1)],output=0) for s in ('entry','exit','add','reduce','trail')},policy=dict(maximum_adds=0),minimum_age={})
    write_json(folder/'checkpoint.json',dict(state=dict(completed=generation,ids=[11,12],population=[payload,payload],archive=[])))
    write_json(folder/'status.json',dict(status='complete',completed_generations=generation))
    return folder,payload


def test_project_summary_arrays_without_loading_trade_ledger(tmp_path,monkeypatch):
    folder,_=fixture(tmp_path);path=folder/'generation-0030'/folder.name/'receipt.json'
    original=d.json.loads;calls=[]
    def checked(value,*a,**kw):
        assert b'episode_profits' not in value;calls.append(len(value));return original(value,*a,**kw)
    monkeypatch.setattr(d.json,'loads',checked)
    result=d.metric_columns(path)
    assert result['net_pnl']==[100.,200.] and len(calls)==len(d.COLUMNS)
    assert max(calls)<100


def test_rank_is_profit_rank_even_when_sort_changes(tmp_path):
    folder,_=fixture(tmp_path);rows=d.load_generation(folder,30)
    assert [r['serial'] for r in d.ordered_rows(rows,'score')]==[11,12]
    assert [r['rank'] for r in d.ordered_rows(rows,'score')]==[2,1]
    assert rows[0]['metrics']['win_rate']==.75 and not rows[1]['eligible']


def test_session_selection_and_no_unchanged_checkpoint_reread(tmp_path,monkeypatch):
    _,payload=fixture(tmp_path);fixture(tmp_path,'2026-07-31')
    write_json(tmp_path/'manifest.json',dict(days=['2026-07-30','2026-07-31'],local_generations=30))
    write_json(tmp_path/'status.json',dict(status='running',completed=2))
    write_json(tmp_path/'active.json',dict(pid=1,creation_time=0));active=tmp_path/'pointer.json';write_json(active,dict(campaign=str(tmp_path)))
    observer=d.DetailObserver(active);observer.refresh();assert observer.read()['payloads'][11]==payload
    original=d.read_progress;reads=[]
    def tracked(path,*a,**kw):
        if path.name=='checkpoint.json':reads.append(path)
        return original(path,*a,**kw)
    monkeypatch.setattr(d,'read_progress',tracked);observer.refresh();assert not reads
    observer.select('2026-07-31');observer.refresh();assert observer.read()['day']=='2026-07-31' and len(reads)==1


@pytest.mark.parametrize('width,height',[(110,38),(70,24)])
def test_detail_panel_has_financial_fields_and_rule_view(tmp_path,width,height):
    folder,payload=fixture(tmp_path);rows=d.load_generation(folder,30)
    data=dict(rows=rows,payloads={11:payload,12:payload},days=[folder.name],day=folder.name,generation=30,running=True,completed_sessions=1,target=30)
    for view in ('financial','rules'):
        output=StringIO();Console(file=output,width=width,height=height,color_system=None).print(d.render(data,width,height,view=view))
        text=output.getvalue();assert 'V21 PHASE 1' in text and 'Net P&L' in text and 'Risk $' in text and 'Positions' in text
        if view=='rules':assert 'OUTPUT' in text and 'minimum age' in text
        assert '\x1b' not in text


def test_missing_historical_genome_is_explicit():
    assert 'not retained' in ' '.join(d.rule_lines(None,'entry'))


def test_population_identity_mismatch_is_not_displayed(tmp_path):
    folder,_=fixture(tmp_path)
    write_json(folder/'generation-0030'/'complete.json',dict(population_sha256='different'))
    with pytest.raises(ValueError,match='identity mismatch'):d.load_generation(folder,30)


def test_default_follows_active_session_but_manual_selection_is_preserved(tmp_path):
    fixture(tmp_path);fixture(tmp_path,'2026-07-31',generation=5)
    write_json(tmp_path/'manifest.json',dict(days=['2026-07-30','2026-07-31'],local_generations=30))
    write_json(tmp_path/'status.json',dict(status='running',completed=1,workers={'2026-07-31':dict(status='running')}))
    active=tmp_path/'pointer.json';write_json(active,dict(campaign=str(tmp_path)))
    observer=d.DetailObserver(active);observer.refresh();assert observer.read()['day']=='2026-07-31'
    observer.select('2026-07-30');observer.refresh();assert observer.read()['day']=='2026-07-30'
    observer.select(None);observer.refresh();assert observer.read()['day']=='2026-07-31'


@pytest.mark.parametrize('width,height',[(110,38),(70,24)])
def test_phase2_canvas_keeps_only_selected_session_progress_and_ranking(tmp_path,width,height):
    folder,payload=fixture(tmp_path);rows=d.load_generation(folder,30)
    workers={day:dict(completed_generations=11,generation=12,total_generations=30,stage='Evaluate rules',rule_tiles=1664,total_rule_tiles=3752,status='running') for day in ['2026-08-07','2026-08-10']}
    data=dict(rows=rows,payloads={11:payload,12:payload},days=[folder.name,*workers],day='2026-08-07',generation=11,running=True,completed_sessions=1,target=30,workers=workers,queued=16)
    output=StringIO();Console(file=output,width=width,height=height,color_system=None).print(d.render(data,width,height))
    text=output.getvalue()
    for expected in ('Session generations','11/30','Evaluate rules','2026-08-07','Net P&L','Risk $'):
        assert expected in text
    assert ('S next session' in text and 'W previous' in text) if width>=105 else 'S/W session' in text
    assert 'Q close' in text and 'U/I detail page' in text
    assert '2026-08-10' not in text and '12/30' not in text and '[########' not in text
    assert len(text.splitlines())==height


def test_switch_clears_old_ranking_before_loading_new_session(tmp_path):
    observer=d.DetailObserver(tmp_path/'pointer.json');observer.data=dict(day='old',rows=[{'serial':1}],payloads={1:{}},generation=30)
    observer.select('new')
    assert observer.read()['day']=='new' and observer.read()['rows']==[] and not observer.read()['payloads']
    assert observer.wake.is_set()


@pytest.mark.parametrize('width,height',[(110,38),(70,24)])
def test_initial_population_progress_uses_actual_batch_count(width,height):
    data=dict(days=['2026-07-30'],day='2026-07-30',generation=0,running=True,target=30,
              session_progress=dict(generation=1,stage='Evaluate rules',completed_local_batches=7,total_local_batches=12,
                                    rule_tiles=100,total_rule_tiles=200))
    output=StringIO();Console(file=output,width=width,height=height,color_system=None).print(d.render(data,width,height))
    text=output.getvalue()
    assert 'Candidate batches' in text and '7/12' in text and '7/2' not in text
