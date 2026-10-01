from dataclasses import replace
import pytest
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from src.trading_runtime.strategy_one_position import ProtectionState
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
from src.trading_runtime.strategy_profit_giveback import profit_giveback
from src.trading_runtime.strategy_profit_giveback_source import validate_profit_giveback_state
from test_strategy_profit_giveback import sample
from test_strategy_profit_giveback_exit import financial


def fixture(strategy_number=31):
    held = financial()
    key = (held.account_id, held.assignment_id, held.ticker)
    source = StrategyOneEntryProposal(held.assignment_id, held.account_id, held.ticker,
        900, 0, 10., 9., 12., 'R3', .5, 800, 'S1', strategy_number)
    state = StrategyOneManagementState(9900, ((key, source),),
        ((key, ProtectionState(9900, 9., 12.)),), (), ((key, 110000),), (), ((key, 1000),))
    return profit_giveback(sample()), state, held


@pytest.mark.parametrize('number', [31, 32])
def test_scalar_state_binds_original_risk_prior_high_and_exact_identity(number):
    witness, state, held = fixture(strategy_number=number)
    assert validate_profit_giveback_state(witness, state, held) == state.submitted[0][1]


@pytest.mark.parametrize('field,value', [('reference_ask',10.01),('initial_stop',8.9),
                                       ('strategy_number',30),('boundary_ms',1000),('ticker','OTHER')])
def test_different_original_entry_is_rejected(field,value):
    witness,state,held=fixture();key,source=state.submitted[0]
    with pytest.raises(ValueError):
        validate_profit_giveback_state(witness,replace(state,submitted=((key,replace(source,**{field:value})),)),held)


@pytest.mark.parametrize('family',['submitted','positions','position_highs','first_held_boundaries'])
def test_missing_or_duplicate_position_family_is_rejected(family):
    witness,state,held=fixture()
    for changed in ((),getattr(state,family)*2):
        with pytest.raises(ValueError):validate_profit_giveback_state(witness,replace(state,**{family:changed}),held)


def test_changed_high_clock_or_account_cannot_bind():
    witness,state,held=fixture();key=state.position_highs[0][0]
    for changed in (replace(state,boundary_ms=9800),replace(state,position_highs=((key,111000),)),
                    replace(state,first_held_boundaries=((key,1100),))):
        with pytest.raises(ValueError):validate_profit_giveback_state(witness,changed,held)
    with pytest.raises(ValueError):validate_profit_giveback_state(witness,state,replace(held,account_id='other'))


@pytest.mark.parametrize('corruption', [None, 'cursor_sequence', 'cursor_boundary',
                                      'snapshot_id', 'entry_account', 'entry_sequence',
                                      'child_strategy', 'snapshot_strategy'])
@pytest.mark.parametrize('number', [31, 32])
def test_checkpoint_reader_routes_and_rejects_changed_authority(monkeypatch, corruption, number):
    """Reader-contract test; mocks do not establish actual DB recovery."""
    from types import SimpleNamespace
    from src.trading_runtime import arte_journal_projection as cursors
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    from src.trading_runtime import arte_followthrough_failure_v4 as entries
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.strategy_profit_giveback_source import load_profit_giveback_checkpoint
    from test_arte_profit_giveback_v4 import project, IDENTITY
    witness,state,held=fixture(strategy_number=number);row=project(strategy_number=number)
    batch='b';prefix=V4CommittedPrefix('run',20,batch,'cursor','running',(batch,))
    cursor={'run_id':'run','event_sequence':7,'batch_id':batch,'boundary_ms':9900,'session_date':'2026-08-04'}
    snapshot={'run_id':'run','snapshot_id':IDENTITY,'checkpoint_sequence':7,'boundary_ms':9900,'session_date':'2026-08-04'}
    entry={'batch_id':batch,'action':'enter_long','reason':'strategy_one_entry','ticker':held.ticker,'reference_price':10.,'invalidation_price':9.}
    event={'account_id':held.account_id,'sequence':6}
    child={'strategy_number':number,'assignment_id':held.assignment_id,'boundary_ms':900}
    if corruption=='child_strategy':child['strategy_number']=63-number
    if corruption=='snapshot_strategy':
        key,proposal=state.submitted[0]
        state=replace(state,submitted=((key,replace(proposal,strategy_number=63-number)),))
    if corruption=='cursor_sequence':cursor['event_sequence']=6
    if corruption=='cursor_boundary':cursor['boundary_ms']=9800
    if corruption=='snapshot_id':snapshot['snapshot_id']='other'
    if corruption=='entry_account':event['account_id']='other'
    if corruption=='entry_sequence':event['sequence']=7
    calls=[]
    def read_cursor(client, ceiling):
        assert ceiling.last_sequence==7 and ceiling.batch_ids==prefix.batch_ids
        calls.append('cursor');return cursor
    def read_snapshot(client,**kwargs):
        assert kwargs=={'run_id':'run','checkpoint_sequence':7}
        calls.append('snapshot');return SimpleNamespace(snapshot=snapshot)
    def restore(rows):calls.append('restore');return state
    def attach(client, committed, restored, **kwargs):
        assert committed==prefix and restored==state
        calls.append('attach');return restored
    def source(client,run,intent_id,**kwargs):
        assert kwargs['prior_batch_id']==batch and intent_id==row['source_entry_intent_id']
        assert kwargs['verified_prefix'] is prefix
        calls.append('entry');return entry,event,child
    monkeypatch.setattr(cursors,'load_latest_backtest_cursor',read_cursor)
    monkeypatch.setattr(snapshots,'load_unattested_manager_snapshot_rows',read_snapshot)
    monkeypatch.setattr(snapshots,'restore_manager_snapshot',restore)
    monkeypatch.setattr(snapshots,'attach_committed_momentum_sources',attach)
    monkeypatch.setattr(entries,'_source_entry',source)
    if corruption:
        with pytest.raises(ValueError):load_profit_giveback_checkpoint(object(),prefix,row,held)
    else:
        assert load_profit_giveback_checkpoint(object(),prefix,row,held)==state
        assert calls==['cursor','snapshot','restore','attach','entry']
