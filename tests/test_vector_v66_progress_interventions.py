from types import SimpleNamespace
from io import StringIO
from rich.console import Console
from research.vectorized_backtest.v66 import progress_interventions as p


def test_exact_changes_include_removal_and_added_nodes():
    assert p.changes({'bias':1,'nodes':[3]}, {'bias':0,'nodes':[3,4]})==['bias: 1 -> 0','nodes[1]: None -> 4']


def test_absent_id_is_not_zero_and_parent_delta_is_frozen():
    child=dict(hypothesis='repair',changes=['entry.bias: 1 -> 0'],parent_metrics={'training':{'net_pnl':-100}},
        history=[{'generation':15,'metrics':{'training':{'net_pnl':-10}}},{'generation':16,'metrics':None}])
    lines='\n'.join(p.detail_lines(child))
    assert 'delta parent $90.00' in lines
    assert 'ID absent' in lines
    assert 'validation' in lines


def test_empty_deferred_review_renders_reason(monkeypatch):
    model=SimpleNamespace(intervention_epoch=20,intervention_child=None,selection=0)
    monkeypatch.setattr(p,'get',lambda _:dict(children=[],rationale='Missing endpoint evidence'))
    stream=StringIO();console=Console(file=stream,width=100,height=25)
    console.print(p.render(model,100,25))
    assert 'Missing endpoint evidence' in stream.getvalue()


def test_navigation_opens_epoch_then_child(monkeypatch):
    model=SimpleNamespace(selection=0,intervention_epoch=None,intervention_child=None,view='interventions')
    monkeypatch.setattr(p,'get',lambda _:[{'epoch':10}])
    p.key(model,'\r',40);assert model.intervention_epoch==10
    monkeypatch.setattr(p,'get',lambda _:dict(children=[dict(serial=19974)]))
    p.key(model,'\r',40);assert model.intervention_child==19974
    p.key(model,'back',40);assert model.intervention_child is None
    p.key(model,'back',40);assert model.intervention_epoch is None
