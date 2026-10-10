"""Saved typed definition selection; explicit read transport/certificate seams."""
from types import SimpleNamespace
import pytest

RUN_ID='c4e247dd-44ad-4657-b879-cad6601c5e50'
ATTEMPT='9d495e81-a3dc-435b-8d3f-11450084c648'

class Reader:
    def __init__(self,terminal=False):self.terminal=terminal;self.queries=[];self.closed=False
    def execute(self,sql):
        assert sql.lstrip().startswith('SELECT ')
        self.queries.append(sql)
        if sql=="SELECT getSetting('readonly')":return '1'
        if 'count() FROM arte.trading_run_v1' in sql:return '1'
        if 'SELECT status FROM arte.trading_commit_v4' in sql:return '{"status":"stopped"}\n' if self.terminal else ''
        raise AssertionError('Unexpected metadata read')
    def close(self):self.closed=True

@pytest.fixture
def saved(monkeypatch):
    from src.backend import replay_run_service as service,backtest_strategy_one_configuration as selection,backtest_v3_clients
    from src.trading_runtime import arte_journal_writer as journal,arte_backtest_definition as definition
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    initialize_numbered_fixed_strategies()
    read=Reader();market=Reader();state=dict(number=108,mismatch=False,calls=[],preflights=[])
    monkeypatch.setattr(journal,'backtest_v4_operator_client_from_env',lambda:read)
    monkeypatch.setattr(backtest_v3_clients,'v3_client',lambda role:market if role=='read' else pytest.fail('Wrong market role'))
    monkeypatch.setattr(journal,'load_typed_run_context',lambda client,run_id:dict(run_id=run_id,session_date='2026-08-04'))
    def parent(client,run_id,run_context):
        return dict(definition=dict(start_local_ms=14400000,end_local_ms=34200000,initial_cash=10000,configuration_revision_id=f"strategy-one-{state['number']}:{ATTEMPT}",structure_book='level-book-v7'),tickers=[])
    monkeypatch.setattr(definition,'load_backtest_definition',parent)
    def certificate(client,number):
        assert client is market;state['calls'].append(number)
        revision=f"strategy-one-{number}:{ATTEMPT}" if not state['mismatch'] else f"strategy-one-{number}:00000000-0000-0000-0000-000000000001"
        return SimpleNamespace(revision=lambda:dict(revision_id=revision,run_plan_id='declared-fixture-plan'),payload=dict(run_plan=dict(run_plan_id='declared-fixture-plan')))
    # Certificate/transport seams only: selected_numbered_revision remains real.
    monkeypatch.setattr(selection,'certify_numbered_configuration',certificate)
    monkeypatch.setattr(selection,'certify_strategy_one_configuration',lambda client:certificate(client,1))
    def preflight(**kwargs):state['preflights'].append(kwargs);return dict(ready=True)
    monkeypatch.setattr(service,'backtest_preflight',preflight)
    def reconstruct(rows,context,revision,checks):
        assert context['run_id']==RUN_ID and rows['definition']['configuration_revision_id']==revision['revision_id']
        assert checks==dict(ready=True);return revision
    monkeypatch.setattr(definition,'reconstruct_backtest_definition_from_arte',reconstruct)
    return service,state,read,market

@pytest.mark.parametrize('number',(1,107,108))
def test_saved_resume_resolves_exact_certified_numbered_release(saved,number):
    service,state,journal,market=saved;state['number']=number
    result=service.ReplayRunService._load_typed_backtest_resume_definition(RUN_ID)
    assert result['revision_id']==f'strategy-one-{number}:{ATTEMPT}'
    assert state['calls']==[number] and journal.closed and market.closed
    call=state['preflights'][0]
    assert call['initial_cash']==10000 and call['tickers']==()
    assert str(call['start_time'])=='04:00:00' and str(call['end_time'])=='09:30:00'
    assert call['configuration_revision']==result

def test_saved_resume_rejects_changed_certified_revision_before_preflight(saved):
    service,state,_,_=saved;state['mismatch']=True
    with pytest.raises(ValueError,match='immutable release'):
        service.ReplayRunService._load_typed_backtest_resume_definition(RUN_ID)
    assert not state['preflights']

def test_saved_resume_rejects_unknown_number_before_certificate(saved):
    service,state,_,_=saved;state['number']=999999
    with pytest.raises(ValueError,match='Unknown immutable numbered'):
        service.ReplayRunService._load_typed_backtest_resume_definition(RUN_ID)
    assert not state['calls'] and not state['preflights']

def test_saved_resume_terminal_row_prevents_selection_and_preflight(saved):
    service,state,journal,_=saved;journal.terminal=True
    with pytest.raises(ValueError,match='Terminal Backtest cannot be resumed'):
        service.ReplayRunService._load_typed_backtest_resume_definition(RUN_ID)
    assert not state['calls'] and not state['preflights'] and journal.closed
