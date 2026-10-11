from dataclasses import replace
from datetime import datetime,timezone

import pytest

from tests.test_backtest_native_channel_qualification import installed,unit  # noqa: F401
from tests.test_backtest_native_channel_manager_recovery import manager_case
from src.backend.backtest_native_channel_management_owner import NativeChannelManagementOwner
from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot


def owner_case(installed, *, strategy_number=1):
    rows,policy,contexts=manager_case(installed,strategy_number=strategy_number)
    context=next(iter(contexts.values()))
    elapsed=context['as_of']-datetime(1970,1,1,tzinfo=timezone.utc)
    stamp=(elapsed.days*86400+elapsed.seconds)*1_000_000+elapsed.microseconds
    q=context['quotes'].row(0,named=True)
    quote=dict(ticker=q['ticker'],session_date=q['session_date'],boundary_ms=rows.snapshot['boundary_ms'],
        quote_valid=1,quote_timestamp_us=stamp,bid_int=q['bid_int'],ask_int=q['ask_int'])
    owner=NativeChannelManagementOwner(run_id=rows.snapshot['run_id'],session_date=context['session_date'],policy=policy)
    args=dict(financial=context['current_financial'],original_entry=context['original_entry'],
        group=context['group'],executions=context['executions'],source_entry_intent_id=context['source_entry_intent_id'],
        expected_conid=context['expected_conid'],boundary_ms=rows.snapshot['boundary_ms'])
    return owner,rows,context,quote,args


def test_owner_stages_native_fills_captures_full_root_and_reproves_every_request(installed):
    owner,rows,context,quote,args=owner_case(installed)
    request=owner.stage(context['prepared'],quote,**args)
    assert request is not None
    assert owner.stage(context['prepared'],quote,**args) is request
    pending=owner.pending_requests(boundary_ms=args['boundary_ms'])
    captured,independent=owner.capture(rows.inherited,journal_batch_id=context['batch_id'])
    assert len(captured.pending)==1 and len(captured.sources)==1 and len(captured.executions)==1
    recovered=reprove_native_channel_manager_snapshot(captured,owner.policy,independent)
    assert recovered[0].acquisition==request.acquisition
    assert dict(recovered[0].decision)==dict(request.decision)
    with pytest.raises(ValueError): owner.complete((replace(request),),boundary_ms=args['boundary_ms'])
    assert owner.pending_requests(boundary_ms=args['boundary_ms'])==pending
    owner.complete(pending,boundary_ms=args['boundary_ms'])
    assert owner.pending_requests(boundary_ms=args['boundary_ms'])==()


def test_bound_feature_owner_reuses_only_exact_acquisition_index(installed):
    from src.backend.backtest_native_channel_session_source import PreparedNativeChannelSession
    from src.trading_runtime.native_acquisition_clock import acquisition_clock_from_entry_executions
    from src.backend.backtest_market_data import market_day_boundary
    owner,_,context,quote,args=owner_case(installed)
    prepared=context['prepared']
    through=prepared.packet.request.through_day_boundary_ms
    prepared=replace(prepared,policy=replace(prepared.policy,
        max_decisions=through//prepared.policy.inputs.decision_interval_ms+1))
    owner=NativeChannelManagementOwner(run_id=owner.run_id,session_date=owner.session_date,
        policy=replace(owner.policy,exit_policy=replace(owner.policy.exit_policy,sequence=prepared.policy,
            quote=replace(owner.policy.exit_policy.quote,max_rows=prepared.policy.max_decisions))))
    session=PreparedNativeChannelSession((args['financial'].ticker,),(prepared,))
    with pytest.raises(ValueError, match='not bound'):
        _ = owner.prepared_feature_session
    owner.bind_feature_session(session,through_day_boundary_ms=through,max_positions=1)
    assert owner.prepared_feature_session is session
    assert owner._feature_session == (session, through, 1)
    clock=acquisition_clock_from_entry_executions(args['group'],args['executions'],
        session_date=owner.session_date,as_of=market_day_boundary(owner.session_date,args['boundary_ms']),
        source_entry_intent_id=args['source_entry_intent_id'],expected_conid=args['expected_conid'])
    before=len(installed[0].sql)
    index=owner._position_index(prepared,clock)
    assert owner._position_index(prepared,clock) is index
    assert owner._position_index(prepared,replace(clock,execution_ids=(*clock.execution_ids,'add'))) is index
    assert len(installed[0].sql)==before
    request=owner.stage(prepared,quote,**args)
    assert request is not None
    assert owner.stage(prepared,quote,**args) is request
    assert owner._position_index(prepared,clock) is index
    with pytest.raises(ValueError): owner._position_index(replace(prepared),clock)
    with pytest.raises(ValueError): owner._position_index(prepared,replace(clock,assignment_id='foreign'))
    with pytest.raises(ValueError): owner.bind_feature_session(session,
        through_day_boundary_ms=through,max_positions=1)
    changed=replace(clock,source_entry_intent_id='new-entry')
    assert owner._position_index(prepared,changed) is not index


def test_individual_retirement_requires_exact_request_and_boundary(installed):
    owner,rows,context,quote,args=owner_case(installed)
    request=owner.stage(context['prepared'],quote,**args)
    frozen=owner.freeze(boundary_ms=args['boundary_ms'])
    for supplied,boundary in ((replace(request),args['boundary_ms']),
                              (request,args['boundary_ms']+100)):
        with pytest.raises(ValueError): owner.complete_one(supplied,boundary_ms=boundary)
        assert owner.pending_requests(boundary_ms=args['boundary_ms'])==(request,)
    owner.complete_one(request,boundary_ms=args['boundary_ms'])
    assert owner.pending_requests(boundary_ms=args['boundary_ms'])==()
    with pytest.raises(ValueError): owner.complete_one(request,boundary_ms=args['boundary_ms'])
    captured,inputs=frozen.capture(rows.inherited,journal_batch_id=context['batch_id'])
    assert len(captured.pending)==1
    assert reprove_native_channel_manager_snapshot(captured,owner.policy,inputs)


def test_individual_retirement_preserves_other_position_and_blocks_clock_advance(installed):
    owner,_,context,quote,args=owner_case(installed)
    first=owner.stage(context['prepared'],quote,**args)
    other_assignment=args['financial'].assignment_id+'-other'
    second_args=dict(args,
        financial=replace(args['financial'],assignment_id=other_assignment),
        original_entry=replace(args['original_entry'],assignment_id=other_assignment),
        group=replace(args['group'],assignment_id=other_assignment))
    second=owner.stage(context['prepared'],quote,**second_args)
    assert first is not second
    owner.complete_one(first,boundary_ms=args['boundary_ms'])
    assert owner.pending_requests(boundary_ms=args['boundary_ms'])==(second,)
    with pytest.raises(ValueError): owner.pending_requests(boundary_ms=args['boundary_ms']+100)
    with pytest.raises(ValueError): owner.complete((first,),boundary_ms=args['boundary_ms'])
    owner.complete((second,),boundary_ms=args['boundary_ms'])
    assert owner.pending_requests(boundary_ms=args['boundary_ms']+100)==()


def test_frozen_pending_capture_survives_live_inventory_drain(installed):
    owner,rows,context,quote,args=owner_case(installed)
    owner.stage(context['prepared'],quote,**args)
    frozen=owner.freeze(boundary_ms=args['boundary_ms'])
    before,inputs=frozen.capture(rows.inherited,journal_batch_id=context['batch_id'])
    owner.complete(owner.pending_requests(boundary_ms=args['boundary_ms']),boundary_ms=args['boundary_ms'])
    after,restored=frozen.capture(rows.inherited,journal_batch_id=context['batch_id'])
    assert before==after and set(inputs)==set(restored)
    assert len(after.pending)==1
    assert reprove_native_channel_manager_snapshot(after,owner.policy,restored)
    with pytest.raises(ValueError): replace(frozen,items=(*frozen.items,*frozen.items))
    with pytest.raises(ValueError): replace(frozen,boundary_ms=frozen.boundary_ms+100)


def test_controller_checkpoint_joins_pending_to_active_inherited_entry(installed):
    from src.backend.backtest_native_channel_management_owner import NativeChannelManagerCheckpoint
    from src.trading_runtime.strategy_one_management_snapshot import restore_manager_snapshot
    owner,rows,context,quote,args=owner_case(installed)
    owner.stage(context['prepared'],quote,**args)
    pending=owner.freeze(boundary_ms=args['boundary_ms'])
    inherited=restore_manager_snapshot(rows.inherited)
    # Use the actual manager-owned source for this controller capture, rather
    # than the older normalized recovery fixture's independently supplied entry.
    key=pending.items[0][0]
    inherited=replace(inherited,submitted=((key,args['original_entry']),))
    joined=NativeChannelManagerCheckpoint(inherited,pending)
    assert joined.pending is pending
    with pytest.raises(ValueError): NativeChannelManagerCheckpoint(replace(inherited,submitted=()),pending)
    with pytest.raises(ValueError): NativeChannelManagerCheckpoint(replace(inherited,positions=()),pending)


def test_owner_rejects_changed_native_fill_source_and_foreign_checkpoint(installed):
    owner,rows,context,quote,args=owner_case(installed)
    with pytest.raises(ValueError): owner.stage(context['prepared'],quote,
        **{**args,'source_entry_intent_id':'foreign'})
    owner.stage(context['prepared'],quote,**args)
    with pytest.raises(ValueError): owner.capture(rows.inherited,journal_batch_id='invalid')
    with pytest.raises(ValueError): owner.pending_requests(boundary_ms=args['boundary_ms']+100)
    with pytest.raises(ValueError): owner.stage(context['prepared'],{**quote,'quote_valid':0},**args)


def test_actual_manager_binds_only_declared_owner_and_stages_its_original_entry(installed):
    from types import SimpleNamespace
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.trading_runtime.runtime import RunMode
    from src.trading_runtime.strategy_one_position import ProtectionState
    owner,_,context,quote,args=owner_case(installed)
    runtime=SimpleNamespace(run_id=owner.run_id,config=SimpleNamespace(strategy_revision=1,
        mode=RunMode.BACKTEST,anchor_date=owner.session_date),
        submit_strategy_one_proposal=lambda *_a:None,submit_strategy_one_add=lambda *_a:None,
        submit_strategy_one_protection=lambda *_a:None,
        _strategy_one_entry_intent=lambda _entry:SimpleNamespace(intent_id=args['source_entry_intent_id']))
    runner=StrategyOneManagementRunner(runtime=runtime,
        evidence=SimpleNamespace(management_evidence=lambda *_a:None),tick_for_ticker=lambda _:0.01)
    with pytest.raises(ValueError): runner.bind_native_channel_management(owner)
    runner.contract=SimpleNamespace(native_channel_management_policy=owner.policy)
    runner.bind_native_channel_management(owner)
    with pytest.raises(ValueError): runner.bind_native_channel_management(owner)
    financial=args['financial']
    key=(financial.account_id,financial.assignment_id,financial.ticker)
    runner._submitted[key]=args['original_entry']
    runner._positions[key]=ProtectionState(args['boundary_ms'],args['original_entry'].initial_stop,
        args['original_entry'].initial_target)
    staged=runner.stage_native_channel_management(context['prepared'],quote,financial=financial,
        group=args['group'],executions=args['executions'],expected_conid=args['expected_conid'],
        boundary_ms=args['boundary_ms'])
    assert runner.native_channel_requests(boundary_ms=args['boundary_ms'])==(staged,)
    with pytest.raises(ValueError): runner.native_channel_requests(boundary_ms=args['boundary_ms']+100)


def test_actual_manager_initialization_selects_declared_owner(installed, monkeypatch):
    from types import SimpleNamespace
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.trading_runtime.runtime import RunMode
    from src.trading_runtime import numbered_fixed_strategy as contracts
    owner, _, _, _, _ = owner_case(installed)
    runtime = SimpleNamespace(run_id=owner.run_id,
        config=SimpleNamespace(strategy_revision=1, mode=RunMode.BACKTEST,
            anchor_date=owner.session_date),
        submit_strategy_one_proposal=lambda *_a: None,
        submit_strategy_one_add=lambda *_a: None,
        submit_strategy_one_protection=lambda *_a: None)
    def build():
        return StrategyOneManagementRunner(runtime=runtime,
            evidence=SimpleNamespace(management_evidence=lambda *_a: None),
            tick_for_ticker=lambda _: 0.01)
    assert build()._native_channel_owner is None
    monkeypatch.setattr(contracts, 'numbered_fixed_strategy',
        lambda _number: SimpleNamespace(native_channel_management_policy=owner.policy))
    runner = build()
    selected = runner._native_channel_owner
    assert type(selected) is NativeChannelManagementOwner
    assert (selected.run_id, selected.session_date, selected.policy) == (
        runtime.run_id, runtime.config.anchor_date, owner.policy)
    assert runner.native_channel_requests(boundary_ms=0) == ()
    with pytest.raises(ValueError): runner.bind_native_channel_management(owner)
    runtime.config.mode = RunMode.REPLAY
    with pytest.raises(ValueError): build()


def test_manager_stages_current_raw_broker_oms_observations(installed):
    import asyncio
    from types import SimpleNamespace
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.trading_runtime.runtime import RunMode
    from src.trading_runtime.strategy_engine import StrategyAssignment
    from src.trading_runtime.strategy_one_position import ProtectionState
    owner, _, context, quote, args = owner_case(installed)
    financial = args['financial']
    entry_group = args['group']
    assignment = StrategyAssignment(financial.assignment_id, 'early-squeeze-strategy', 1,
        financial.account_id, financial.ticker, args['expected_conid'],
        financial.status, financial.permissions, {})
    async def positions(_account): return []
    async def trades(): return args['executions']
    broker = SimpleNamespace(position_quantity=lambda *_args: financial.position_quantity,
        positions=positions, trades=trades)
    oms = SimpleNamespace(snapshots=lambda: (entry_group,),
        snapshots_for_assignment=lambda *_args: (entry_group,))
    runtime = SimpleNamespace(run_id=owner.run_id, broker=broker, order_manager=oms,
        config=SimpleNamespace(strategy_id='early-squeeze-strategy', strategy_revision=1,
            mode=RunMode.BACKTEST, anchor_date=owner.session_date),
        submit_strategy_one_proposal=lambda *_a: None, submit_strategy_one_add=lambda *_a: None,
        submit_strategy_one_protection=lambda *_a: None,
        _strategy_one_entry_intent=lambda _entry: SimpleNamespace(intent_id=args['source_entry_intent_id']))
    manager = StrategyOneManagementRunner(runtime=runtime,
        evidence=SimpleNamespace(management_evidence=lambda *_a: None), tick_for_ticker=lambda _: 0.01)
    manager.contract=SimpleNamespace(native_channel_management_policy=owner.policy)
    manager.bind_native_channel_management(owner)
    key = (financial.account_id, financial.assignment_id, financial.ticker)
    manager._submitted[key] = args['original_entry']
    manager._positions[key] = ProtectionState(args['boundary_ms'], args['original_entry'].initial_stop,
        args['original_entry'].initial_target)
    staged = asyncio.run(manager.stage_current_native_channel_management(context['prepared'], quote,
        assignment=assignment, boundary_ms=args['boundary_ms']))
    assert staged is not None
    assert manager.native_channel_requests(boundary_ms=args['boundary_ms']) == (staged,)
    assert owner.freeze(boundary_ms=args['boundary_ms']).items[0][1].group is entry_group
    with pytest.raises(ValueError):
        asyncio.run(manager.stage_current_native_channel_management(context['prepared'], quote,
            assignment=replace(assignment, strategy_revision=2), boundary_ms=args['boundary_ms']))
