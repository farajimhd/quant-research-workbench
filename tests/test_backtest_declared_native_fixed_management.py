"""Actual declared manager/reducers with prepared source and explicit ACK seam.

No installed future actor, native checkpoint or financial acceptance is claimed.
"""
import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from test_backtest_declared_native_fixed_entry import parent, Source, financial, RUN
from src.backend.backtest_declared_native_fixed_plan import load_declared_entry_source_plan
from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation
from src.backend.backtest_declared_native_fixed_management import (
    DeclaredNativeFixedManagementRunner, DeclaredManagementPolicy,
    DeclaredProfitArmReference, declared_management_exit,
)
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
from src.trading_runtime.strategy_one_position import confirm_protection_transition, ResistanceBreak
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeCandle
from src.trading_runtime.strategy_profit_giveback_arm import ProfitArmCandidate, profit_arm_candidate
from src.trading_runtime.entry_spread_risk import exact_epoch_us


class Runtime:
    """Explicit prepared callback seam; no fake broker/Portfolio certification."""
    def __init__(self):
        self.entries, self.exits, self.protections, self.sessions = [], [], [], []
        self.commands = []
        self.approve = True
        self.reject_protection = False

    async def submit_declared_entry_proposal(self, proposal, view):
        self.entries.append(proposal)
        return [{"order_group": object() if self.approve else None, "decision": {"status": "approved" if self.approve else "rejected"}}]

    async def submit_declared_management(self, command):
        from src.trading_runtime.declared_native_management_command import (DeclaredExitCommand, DeclaredSessionCommand, DeclaredProtectionCommand)
        result=command.replay()
        self.commands.append(command)
        if type(command) is DeclaredExitCommand:
            self.exits.append((command.kind,command.witness,command.context.source.intent_id,command.inputs.prior_arm))
        elif type(command) is DeclaredSessionCommand:
            self.sessions.append((result,command.context.source.intent_id))
        elif type(command) is DeclaredProtectionCommand:
            previous=command.inputs.previous
            self.protections.append((previous,result))
            if self.reject_protection:
                raise RuntimeError("ACK unavailable")
            return confirm_protection_transition(previous,result,
                target_confirmed=result.target_amendment is not None,stop_confirmed=result.stop_amendment is not None)
        else:
            raise TypeError('Foreign management command')


class Evidence:
    def __init__(self):
        self.rows = {}

    async def management_evidence(self, ticker, resolutions, *, boundary_ms):
        return self.rows[boundary_ms]


class Lookup:
    def __init__(self):
        self.windows = {}

    def window_at(self, ticker, boundary):
        return self.windows.get((ticker, boundary))


@pytest.fixture
def manager(parent):
    source = load_declared_entry_source_plan(parent, client=Source(parent))
    prep = DeclaredEntryPreparation(RUN, "assignment", "account", source)
    runtime, evidence, lookup = Runtime(), Evidence(), Lookup()
    manager = DeclaredNativeFixedManagementRunner(runtime=runtime, evidence=evidence,
        preparation=prep, tick_for_ticker=lambda ticker: .01)
    refs = {}
    for ticker, _ in parent.momentum.keys:
        refs[ticker] = dict(source_build_id=parent.market.build_id, source_market_plan_token=parent.market.token)
        for stage, field in (("bars", "source_bars_attempt_id"), ("technical", "source_indicators_attempt_id"), ("broker_100ms", "source_liquidity_attempt_id")):
            refs[ticker][field] = next(u.attempt_id for u in parent.market.units if u.stage == stage and u.ticker == ticker)
    manager.bind_liquidity_source(lookup, refs)
    view = financial(parent)
    proposal = prep.propose(*parent.momentum.keys[0], view).proposal
    return manager, runtime, evidence, lookup, proposal, view


def rows(manager, proposal, boundary, *, bid=None, close=None, line=.1, signal=.05, high=None, breaks=()):
    bid = proposal.reference_ask if bid is None else bid
    close = round(bid * 10000) if close is None else close
    evidence = StrategyOneManagementEvidence(proposal.ticker, boundary, bid, bid+.0001,
        True, None, None, tuple(breaks), ())
    at = exact_epoch_us(market_day_boundary(date.fromisoformat(manager.preparation.source.parent.market.sessions[0]), boundary))
    return evidence, {100: {"price_valid":1, "high_int": high or close, "quote_valid":1, "quote_timestamp_us":at},
        5000: {"boundary_ms": boundary//5000*5000, "price_valid":1, "close_int":close, "macd_line":line, "macd_signal":signal}}


async def held(bundle):
    manager, runtime, evidence, lookup, proposal, view = bundle
    await manager.on_entry_proposal(proposal, view)
    view = replace(view, position_quantity=10., completed_entries=1)
    await manager.on_management(view, {}, proposal.boundary_ms+100)
    return view


def test_actual_manager_preserves_original_firstheld_and_excludes_fill_bucket_high(manager):
    async def run():
        m, runtime, evidence, _, p, _ = manager
        view = await held(manager)
        state = m.capture_state(boundary_ms=p.boundary_ms+100)
        assert dict(state.first_held_boundaries).popitem()[1] == p.boundary_ms+100
        assert dict(state.position_highs).popitem()[1] == round(p.reference_ask*10000)
        assert not m.profit_arming_requests(boundary_ms=p.boundary_ms+100)
        ev, frame = rows(m,p,35000,bid=p.initial_stop,line=.01,signal=.02)
        evidence.rows[35000] = ev
        await m.on_management(view,frame,35000)
        assert runtime.exits == []  # First5s bucket overlaps first-held.
        ev, frame = rows(m,p,40000,bid=p.initial_stop,line=.01,signal=.02)
        evidence.rows[40000] = ev
        await m.on_management(view,frame,40000)
        assert runtime.exits[0][0] == "zero_regime"
        assert runtime.exits[0][1].reference_ask == p.reference_ask
        assert runtime.exits[0][1].initial_stop == p.initial_stop
        assert runtime.exits[0][2] == p.intent_id
    asyncio.run(run())


def test_rejected_entry_ack_never_owns_position(manager):
    async def run():
        m, runtime, _, _, proposal, view = manager
        runtime.approve = False
        await m.on_entry_proposal(proposal,view)
        assert not m.capture_state(boundary_ms=proposal.boundary_ms).submitted
        with pytest.raises(RuntimeError,match="own original"):
            await m.on_management(replace(view,position_quantity=1.),{},proposal.boundary_ms+100)
    asyncio.run(run())


def test_winning_quote_keeps_target_and_no_time_alone_exit(manager):
    async def run():
        m,runtime,evidence,_,p,_=manager
        view=await held(manager)
        for boundary in (40000,95000):
            ev,frame=rows(m,p,boundary,bid=p.reference_ask+.01,line=.2,signal=.1)
            evidence.rows[boundary]=ev
            await m.on_management(view,frame,boundary)
        state=m.capture_state(boundary_ms=95000)
        assert dict(state.positions).popitem()[1].target == p.initial_target
        assert runtime.exits == []
    asyncio.run(run())


def test_real_profit_arm_selector_matches_legacy_control_and_requires_prior_confirmed_boundary(manager):
    async def run():
        m,runtime,evidence,_,p,_=manager
        view=await held(manager)
        risk=Decimal(str(p.reference_ask))-Decimal(str(p.initial_stop))
        high=int((Decimal(str(p.reference_ask))+risk)*10000)
        ev,frame=rows(m,p,40000,bid=p.reference_ask,high=high)
        evidence.rows[40000]=ev
        await m.on_management(view,frame,40000)
        requests=m.profit_arming_requests(boundary_ms=40000)
        candidate=requests[0][0]
        # Test-only old typed capture control; never submitted/relabelled.
        from src.backend.backtest_strategy_one_management import StrategyOneManagementState
        from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
        old=StrategyOneEntryProposal(p.assignment_id,p.account_id,p.ticker,p.boundary_ms,p.episode_start_ms,
            p.reference_ask,p.initial_stop,p.initial_target,p.target_level_id,p.frozen_gap,p.bos_break_boundary_ms,p.bos_support_level_id,strategy_number=59)
        state=m.capture_state(boundary_ms=40000)
        control=StrategyOneManagementState(state.boundary_ms,((state.submitted[0][0],old),),state.positions,state.pending_breaks,
            state.position_highs,state.closed_positions,state.first_held_boundaries)
        assert profit_arm_candidate(control,view,already_checkpointed=False)==candidate
        reference=DeclaredProfitArmReference(RUN,state.source_token,candidate,
            "11111111-1111-4111-8111-111111111111",1,"22222222-2222-4222-8222-222222222222",state.content_hash())
        m.accept_profit_arming_references(requests,(reference,),boundary_ms=40000)
        floor=p.reference_ask+float(risk)/2
        ev,frame=rows(m,p,45000,bid=floor,line=.01,signal=.02)
        evidence.rows[45000]=ev
        await m.on_management(view,frame,45000)
        assert runtime.exits[-1][0]=="profit_giveback"
        assert runtime.exits[-1][3]==reference
    asyncio.run(run())


def test_liquidity_request_is_deferred_exact_retry_and_blocks_later_boundary(manager):
    async def run():
        m,runtime,evidence,lookup,p,_=manager
        view=await held(manager)
        # After early1m, positive signal disables inherited late-zero regime.
        at=100100
        candles=tuple(LiquidityFadeCandle(b,count) for b,count in zip((85000,90000,95000,100000),(100,100,25,25)))
        lookup.windows[(p.ticker,at)]=SimpleNamespace(candles=candles)
        ev,frame=rows(m,p,at,bid=(p.reference_ask+p.initial_stop)/2,line=.01,signal=.02)
        evidence.rows[at]=ev
        await m.on_management(view,frame,at)
        request=m.liquidity_fade_requests(boundary_ms=at)
        assert request and request[0].kind=="liquidity_fade"
        assert runtime.exits==[]
        await m.on_management(view,frame,at)
        assert request==m.liquidity_fade_requests(boundary_ms=at)
        with pytest.raises(RuntimeError,match="finish before"):
            await m.on_management(view,{},at+100)
        m.complete_liquidity_fade_requests(request,boundary_ms=at)
    asyncio.run(run())


def test_prepared_recovery_preserves_protection_and_reproves_own_entry(manager):
    async def run():
        m,runtime,evidence,lookup,p,predecessor=manager
        view=await held(manager)
        ev,frame=rows(m,p,40000)
        evidence.rows[40000]=ev
        await m.on_management(view,frame,40000)
        state=m.capture_state(boundary_ms=40000)
        other=DeclaredNativeFixedManagementRunner(runtime=Runtime(),evidence=evidence,preparation=m.preparation,tick_for_ticker=lambda _: .01)
        key=state.submitted[0][0]
        other.restore_prepared_state(state,predecessor_financials=((key,predecessor),))
        assert other.capture_state(boundary_ms=40000)==state
        assert other._profit_arm_references == {}
        with pytest.raises(RuntimeError,match="already active"):
            other.restore_prepared_state(state,predecessor_financials=((key,predecessor),))
        empty=DeclaredNativeFixedManagementRunner(runtime=Runtime(),evidence=evidence,preparation=m.preparation,tick_for_ticker=lambda _: .01)
        with pytest.raises(ValueError,match="run/source"):
            empty.restore_prepared_state(replace(state,run_id="22222222-2222-4222-8222-222222222222"),predecessor_financials=((key,predecessor),))
        with pytest.raises(ValueError):
            empty.restore_prepared_state(state,predecessor_financials=((key,replace(predecessor,pending_entry=True)),))
        with pytest.raises(ValueError,match="independent source resolver"):
            empty.restore_state(state,source_authority=object())
    asyncio.run(run())


@pytest.mark.parametrize("mutation",("missing-held","future-held","source-token","economic-source","protection"))
def test_recovery_rejects_typed_state_corruption(manager,mutation):
    async def run():
        m,_,evidence,_,p,predecessor=manager
        await held(manager)
        state=m.capture_state(boundary_ms=p.boundary_ms+100)
        key=state.submitted[0][0]
        if mutation=="missing-held": state=replace(state,first_held_boundaries=())
        elif mutation=="future-held": state=replace(state,first_held_boundaries=((key,40000),))
        elif mutation=="source-token": state=replace(state,source_token="f"*64)
        elif mutation=="economic-source": state=replace(state,submitted=((key,replace(p,reference_ask=p.reference_ask+.01)),))
        else: state=replace(state,positions=((key,replace(state.positions[0][1],stop=p.initial_target+1)),))
        empty=DeclaredNativeFixedManagementRunner(runtime=Runtime(),evidence=evidence,preparation=m.preparation,tick_for_ticker=lambda _: .01)
        with pytest.raises(ValueError): empty.restore_prepared_state(state,predecessor_financials=((key,predecessor),))
        assert empty._submitted=={}
    asyncio.run(run())


def test_session_liquidation_precedes_management_evidence_and_no_account_crossover(manager):
    async def run():
        m,runtime,_,_,p,view=manager
        view=await held(manager)
        with pytest.raises(ValueError): await m.on_management(replace(view,account_id="foreign"),{},40000)
        await m.on_management(view,{},19740000)
        assert runtime.sessions==[(19740000,p.intent_id)]
        assert runtime.exits==[]
    asyncio.run(run())


def test_actual_protection_reducer_retries_same_boundary_after_missing_ack(manager):
    async def run():
        m,runtime,evidence,_,p,_=manager
        view=await held(manager)
        breaks=tuple(ResistanceBreak(40000,dict(unified_level_id=f"R{index}",
            lower=p.reference_ask+index*.02,upper=p.reference_ask+index*.02+.001,
            role="resistance",side="resistance")) for index in (1,2,3))
        ev,frame=rows(m,p,40000,bid=p.reference_ask+.2,breaks=breaks)
        evidence.rows[40000]=ev
        runtime.reject_protection=True
        with pytest.raises(RuntimeError,match="ACK unavailable"):
            await m.on_management(view,frame,40000)
        failed=m.capture_state(boundary_ms=40000)
        assert failed.positions[0][1].stop==p.initial_stop
        assert len(failed.pending_breaks[0][1])==3
        runtime.reject_protection=False
        await m.on_management(view,frame,40000)
        confirmed=m.capture_state(boundary_ms=40000)
        assert confirmed.positions[0][1].stop>p.initial_stop
        assert confirmed.positions[0][1].target==p.initial_target
        assert confirmed.first_held_boundaries==failed.first_held_boundaries
        assert runtime.protections[0][1]==runtime.protections[1][1]
    asyncio.run(run())


def test_half_risk_deferred_priority_and_checkpoint_reference_corruption(parent):
    policy=DeclaredManagementPolicy(parent.capabilities)
    completed=FollowThroughFailureInput(100100,30100,10.,9.,100000,95000,True,.01,.02,9.5,9.51,0,1.,False)
    candles=tuple(LiquidityFadeCandle(b,count) for b,count in zip((85000,90000,95000,100000),(100,100,40,40)))
    assert declared_management_exit(completed,policy=policy,candles=candles)[0]=="half_risk_liquidity"
    assert declared_management_exit(replace(completed,bid=9.6,completed_five_second_close_int=96000),policy=policy,candles=candles) is None
    with pytest.raises(ValueError):
        DeclaredProfitArmReference(RUN,"f"*64,object(),RUN,True,RUN,"f"*64)


@pytest.mark.parametrize("concern",("premarket_failure_policy","profit_protection_policy","confirmed_ah_failure_policy","liquidity_fade_policy","half_risk_liquidity_policy"))
def test_foreign_inherited_exit_policy_rejected(parent,concern):
    import json
    payload=parent.capabilities.payload()["inherited"]
    payload["policies"][concern]["private_override"]=True
    changed=replace(parent.capabilities,inherited_json=json.dumps(payload,sort_keys=True,separators=(",",":")))
    with pytest.raises(ValueError,match="Unsupported"):
        DeclaredManagementPolicy(changed)


def test_pure_priority_and_pending_missing_source_guards(parent):
    policy=DeclaredManagementPolicy(parent.capabilities)
    base=FollowThroughFailureInput(43250000,43230100,10.,9.,43250000,97500,True,.01,.02,9.75,9.76,0,1.,False)
    assert declared_management_exit(base,policy=policy)[0]=="early_original_risk"
    ten=dict(boundary_ms=43250000,price_valid=1,macd_line=.01,macd_signal=.02)
    assert declared_management_exit(base,policy=policy,ten=ten)[0]=="confirmed_ah"
    assert declared_management_exit(replace(base,completed_five_second_close_int=95000,bid=9.5),policy=policy,ten=ten)[0]=="zero_regime"
    for field,value in (("pending_exit",True),("quote_age_us",1000001),("macd_signal",None),("price_valid",False)):
        assert declared_management_exit(replace(base,**{field:value}),policy=policy) is None
    arm=ProfitArmCandidate("account","assignment","AAA",43250000,43230100,10.,9.,110000)
    same=replace(base,completed_five_second_close_int=104000,bid=10.4)
    assert declared_management_exit(same,policy=policy,prior_arm=arm) is None
