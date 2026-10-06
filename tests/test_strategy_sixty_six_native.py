"""Actual native consumers with synthetic scalar producer graphs; no DB attestation.

Exit factories receive genuine registered66 identity. Original entry rows are
constructed synthetic producer evidence, never relabeled published42 rows.
"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
from src.trading_runtime.strategy_followthrough_exit import validate_witness
from src.trading_runtime.arte_followthrough_failure_v4 import seal_followthrough_rows, restore_failure
from src.trading_runtime.arte_journal_writer import _sealed_families
from test_strategy_fifty_failure_route import fixture as producer_graph


def witness(held=43_211_100, boundary=43_280_000):
    return FollowThroughFailure(boundary, held, 10.01, 9.89, 99_700,
                                .01, .02, 9.97, 9.98, 100_000)


def test_real_async_bootstrap_selects_unchanged42_momentum_parent(monkeypatch):
    from test_strategy_session_momentum_bootstrap import test_real_session_bootstrap_reaches_exact_selected_momentum_parent
    test_real_session_bootstrap_reaches_exact_selected_momentum_parent(monkeypatch,66)


@pytest.mark.parametrize('change', [None,'missing','foreign_batch','tampered'])
def test_cold_scalar_read_verifies_committed_prefix_and_original_anchor_hash(change):
    from src.trading_runtime.arte_followthrough_failure_v4 import FAILURE,load_followthrough_failure
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_writer import typed_row
    from test_liquidity_fade_native_reader import Client
    _,base,row,*_ = producer_graph(witness(),66)
    stored = typed_row(FAILURE.name,row)
    for name,dtype in FAILURE.columns:
        if dtype == 'UInt64': stored[name] = str(stored[name])
    prefix = V4CommittedPrefix(base.run_id,2,base.batch_id,'cursor','running',(base.batch_id,))
    rows = [stored]
    if change == 'missing': rows = []
    elif change == 'foreign_batch': stored['batch_id'] = '00000000-0000-0000-0000-000000000099'
    elif change == 'tampered': stored['reference_ask'] += .01
    client = Client(rows)
    if change:
        with pytest.raises(RuntimeError): load_followthrough_failure(client,prefix,row['parent_record_id'])
    else:
        _,restored = load_followthrough_failure(client,prefix,row['parent_record_id'])
        assert restored == witness()
    assert len(client.queries) == 1


@pytest.mark.parametrize('held,boundary', [(31_100, 100_000), (43_211_100,43_280_000),
                                         (43_211_100,57_295_000)])
def test_new_quarter_loss_roundtrips_while_original42_rejects(held,boundary):
    w = witness(held,boundary)
    with pytest.raises(ValueError, match='pinned rule'):
        validate_witness(w,strategy_number=42)
    intent,base,row,source,event,entry = producer_graph(w,66)
    families = dict(_sealed_families(base))
    sealed = seal_followthrough_rows(None,(row,),
        (source,*families['trading_strategy_intent_v1']), (event,*base.events),(entry,))
    assert sealed[0]['strategy_number'] == 66
    assert restore_failure(sealed[0]) == w
    assert intent.quantity == 10. and intent.reference_price == w.bid


@pytest.mark.parametrize('change', ['missing','duplicate','foreign','future','reference','stop','assignment','witness'])
def test_native_sealer_rejects_missing_foreign_or_tampered_original_graph(change):
    _,base,row,source,event,entry = producer_graph(witness(),66)
    families = dict(_sealed_families(base))
    rows = (row,)
    if change == 'missing': rows = ()
    elif change == 'duplicate': rows = (row,row)
    elif change == 'foreign': entry = {**entry,'strategy_number':42}
    elif change == 'future': entry = {**entry,'boundary_ms':row['first_held_boundary_ms']}
    elif change == 'reference': source = {**source,'reference_price':10.02}
    elif change == 'stop': source = {**source,'invalidation_price':9.88}
    elif change == 'assignment': entry = {**entry,'assignment_id':'foreign'}
    else: rows = ({**row,'completed_close_int':100_100},)
    with pytest.raises(ValueError):
        seal_followthrough_rows(None,rows,(source,*families['trading_strategy_intent_v1']),
            (event,*base.events),(entry,))


def test_oms_scalar_restores_registered66_quantity_and_assignment():
    from src.trading_runtime.arte_oms_projection import _approved_strategy_one_oms_intent
    intent,base,row,*_ = producer_graph(witness(),66)
    group = SimpleNamespace(sequence=2,group=dict(account_id='DU1',strategy_revision=66,group_id='group'))
    source = SimpleNamespace(intent=intent,record_id=row['parent_record_id'],batch_id=row['batch_id'])
    history = SimpleNamespace(run_id=base.run_id,records=(),through_sequence=2)
    reservation = dict(account_id='DU1',intent_id=intent.intent_id,reservation_id='r',decision_id='d',
        account_key='cash',assignment_id='assignment-1',quantity=10.)
    decision = dict(reservation_id='r',decision_id='d',account_key='cash',status='approved',
        policy_id='policy',policy_revision=1,requested_quantity=10.)
    approved,_ = _approved_strategy_one_oms_intent(group,source,history,reservation,decision,followthrough_row=row)
    assert approved.quantity == 10. and approved.metadata['assignment_id'] == 'assignment-1'
    with pytest.raises(ValueError):
        _approved_strategy_one_oms_intent(group,source,history,reservation,decision,followthrough_row=None)


@pytest.mark.parametrize('inherited', [False,True])
def test_actual_manager_after_minute_extension_and_inherited_zero_regime_priority(monkeypatch,inherited):
    from test_strategy_forty_two_management import prepared_manager
    manager,w,financial,rows = prepared_manager(66)
    manager.contract = numbered_fixed_strategy(66)
    key = financial.account_id,financial.assignment_id,financial.ticker
    source = manager._submitted[key]
    at = w.completed_five_second_boundary_ms
    manager._first_held_boundaries[key] = at-120_000
    price = int((3*source.reference_ask+source.initial_stop)/4*10_000)-1
    if inherited:
        price = int((source.reference_ask+source.initial_stop)/2*10_000)-1
        from src.trading_runtime import all_held_original_risk_failure as extension
        def forbidden(*args,**kwargs):
            raise AssertionError('Inherited winner must return before extension evaluation')
        monkeypatch.setattr(extension,'all_held_original_risk_failure',forbidden)
    frame = rows(at,completed=True,age=48)
    frame[5000].update(close_int=price,macd_line=-.02 if inherited else .01,
                       macd_signal=-.01 if inherited else .02)
    evidence = asyncio.run(manager.evidence.management_evidence(financial.ticker,frame,boundary_ms=at))
    manager.evidence.management_evidence = AsyncMock(return_value=replace(evidence,bid=price/10_000,ask=price/10_000+.01))
    asyncio.run(manager.on_management(financial,frame,at))
    manager.runtime.submit_followthrough_failure.assert_awaited_once()
    emitted = manager.runtime.submit_followthrough_failure.await_args.args[1]
    validate_witness(emitted,strategy_number=66)
    if inherited: validate_witness(emitted,strategy_number=42)
    else:
        with pytest.raises(ValueError,match='pinned rule'): validate_witness(emitted,strategy_number=42)
    manager.runtime.submit_profit_giveback.assert_not_awaited()
    manager.runtime.submit_confirmed_ah_failure.assert_not_awaited()
    manager.runtime.submit_liquidity_fade_failure.assert_not_awaited()


def test_actual_inherited_liquidity_checkpoint_precedes_new_extension(monkeypatch):
    from test_strategy_forty_two_management import prepared_manager
    from src.trading_runtime import all_held_original_risk_failure as extension
    manager,w,financial,rows = prepared_manager(66,counts=(57,18,10,5))
    manager.contract = numbered_fixed_strategy(66)
    key = financial.account_id,financial.assignment_id,financial.ticker
    at = w.completed_five_second_boundary_ms
    manager._first_held_boundaries[key] = at-120_000
    frame = rows(at,completed=True,age=48)
    frame[5000].update(close_int=23100,macd_line=.01,macd_signal=.02)
    evidence = asyncio.run(manager.evidence.management_evidence(financial.ticker,frame,boundary_ms=at))
    manager.evidence.management_evidence = AsyncMock(return_value=replace(evidence,bid=2.31,ask=2.32))
    def forbidden(*args,**kwargs):
        raise AssertionError('Inherited checkpoint must return before extension evaluation')
    monkeypatch.setattr(extension,'all_held_original_risk_failure',forbidden)
    asyncio.run(manager.on_management(financial,frame,at))
    assert len(manager.liquidity_fade_requests(boundary_ms=at)) == 1
    manager.runtime.submit_followthrough_failure.assert_not_awaited()
def test66_ordinary_profile_real_writer_and_cold_preflight():
    """Actual generic preflights; external catalog authority is explicitly synthetic."""
    from test_ladder_waiting_profile import Catalog
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES
    from src.trading_runtime.arte_squeeze_ladder_schema import BINDING
    from src.backend.backtest_fixed_journal_bootstrap import _v4_cold_reader_preflight
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    contract = numbered_fixed_strategy(66)
    assert contract.all_held_original_risk_policy is not None
    client = Catalog()
    client.automatic_ladder_profile = False
    client.entry_spread_risk_profile = False
    client.user = 'backtest_v4_runner'
    client.writable = set(writer.v4_journal_write_tables())
    client.required = client.writable | set(MARKET_READ_TABLES) | {
        table.name for table in writer.fixed_backtest_v2_contracts()}
    writer._v4_preflight(client)
    _v4_cold_reader_preflight(client)
    assert not any(BINDING.name in sql for sql in client.statements)
    client.user = 'backtest_v4_waiting_ladder_runner'
    with pytest.raises(RuntimeError, match='unexpected principal'):
        _v4_cold_reader_preflight(client)
