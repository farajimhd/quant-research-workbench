"""Actual shared cash/OMS/broker component path; no installed native authority."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioAccountProfile, PortfolioPolicy
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from tests.test_fixed_structural_lots import compiled
from tests.test_trading_runtime import quote


class Actor:
    async def start(self, selected=True):
        self.entry, original, selected_intent, self.intervals = compiled()
        self.intent = selected_intent if selected else original
        self.at = self.intent.event_time
        self.run_id = str(uuid4())
        self.journal = BacktestMemoryJournal(run_id=self.run_id)
        self.broker = SimulatedBrokerAdapter(['account'], SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST, initial_time=self.at)
        await self.broker.initialize()
        self.risk = RiskAuthority()
        await self.risk.prime(self.broker,['account'])
        # Explicit fixture PM/AH permission, not a claim about an installed
        # immutable full configuration. Both comparisons use identical policy.
        profile=PortfolioAccountProfile('cash','account','backtest','simulated',PortfolioPolicy(allow_outside_rth=True))
        self.portfolio = PortfolioManagementEngine((profile,),
            journal=self.journal,run_id=self.run_id,strategy_id='component-fixture',strategy_revision=1,
            event_clock=lambda:self.at)
        await self.portfolio.synchronize(self.broker)
        self.journal.append(run_id=self.run_id, category='strategy',entity_type='strategy_intent',
            entity_id=self.intent.intent_id,account_id='account',event_time=self.at,payload=self.intent.payload())
        self.decision, self.approved = await self.portfolio.approve(self.intent,account_id='account',
            assignment_id=self.entry.proposal.assignment_id)
        assert self.approved is not None, self.decision.reasons
        async def updated(snapshot):
            self.portfolio.on_order_group_update(snapshot)
        self.oms = OrderManagementEngine(broker=self.broker,
            planner=lambda intent, account_id, event: IbkrStrategyOrderPlanner().plan(
                account_id=account_id,instrument=InstrumentContract('AAA',1,'AAA','STK','USD'),
                intent=intent,strategy_id='component-fixture',strategy_revision=1),
            risk=self.risk,journal=self.journal,run_id=self.run_id,strategy_id='component-fixture',
            strategy_revision=1,state_callback=updated,causal_execution_clock=True)
        self.oms.on_market_snapshot(ExecutionMarketSnapshot('AAA',10.,10.01,.01,self.at,'qmd-history'))
        snapshot = await self.oms.submit_intent(self.approved,account_id='account',event=None)
        self.group = self.oms._groups[snapshot.group_id]
        self.executions=[]
        return self

    async def step(self,bid,ask,*,ask_size=10000,bid_size=10000):
        self.at += timedelta(milliseconds=100)
        event=replace(quote(bid=bid,ask=ask,ask_size=ask_size,bid_size=bid_size),
            ticker='AAA',raw={'conid':1},ts=self.at,ingest_ts=self.at)
        self.oms.on_market_snapshot(ExecutionMarketSnapshot('AAA',bid,ask,.01,self.at,'qmd-history'))
        self.executions.extend(await self.broker.on_market_event(event))
        await self.oms.reconcile()
        await self.portfolio.synchronize(self.broker)
        return event

    async def close(self):
        await self.oms.close()
        await asyncio.sleep(0)
        self.journal.close()

    async def recover_oms(self):
        await self.oms.close()
        async def updated(snapshot):
            self.portfolio.on_order_group_update(snapshot)
        risk=RiskAuthority()
        await risk.prime(self.broker,['account'])
        self.oms=OrderManagementEngine(broker=self.broker,
            planner=lambda intent, account_id, event: IbkrStrategyOrderPlanner().plan(
                account_id=account_id,instrument=InstrumentContract('AAA',1,'AAA','STK','USD'),
                intent=intent,strategy_id='component-fixture',strategy_revision=1),
            risk=risk,journal=self.journal,run_id=self.run_id,strategy_id='component-fixture',
            strategy_revision=1,state_callback=updated,causal_execution_clock=True)
        self.oms.on_market_snapshot(ExecutionMarketSnapshot('AAA',10.,10.01,.01,self.at,'qmd-history'))
        recovered=await self.oms.recover()
        assert len(recovered)==1
        self.group=self.oms._groups[recovered[0].group_id]


def test_one_real_portfolio_admission_sizes_default_and_three_lots_equally():
    async def exercise():
        actors=[]
        try:
            for selected in (False,True):
                actor=await Actor().start(selected)
                actors.append(actor)
                assert actor.intent.quantity == 0.
                assert actor.approved.quantity > 0
                records=tuple(actor.journal.records(actor.run_id))
                admissions=[r for r in records if r.entity_type=='portfolio_reservation'
                            and r.payload.get('event')=='reservation_created']
                assert len(admissions)==1
                assert sum(o.quantity for o in actor.group.orders if o.side=='BUY')==actor.approved.quantity
                await actor.step(10.,10.01)
                assert actor.group.filled_quantity == actor.approved.quantity
                assert len(actor.executions)==(3 if selected else 1)
                assert sum(fill.commission for fill in actor.executions)==pytest.approx(
                    sum(max(float(fill.size)*.005,1.) for fill in actor.executions))
                assert actor.portfolio.states['account'].profile.policy.entry_fee_buffer_bps==50.
            default,three=actors
            assert default.approved.quantity==three.approved.quantity
            assert sum(fill.commission for fill in three.executions)>sum(fill.commission for fill in default.executions)
            assert len(default.group.plan.broker_batches)==1
            assert len(three.group.plan.broker_batches)==3
        finally:
            for actor in actors:
                await actor.close()
    asyncio.run(exercise())


def test_partial_target_then_remaining_stop_keeps_fixed_lots_and_cash_exact():
    async def exercise():
        actor=await Actor().start()
        try:
            await actor.step(10.,10.01)
            await actor.step(10.31,10.32)
            targets=[fill for fill in actor.executions if fill.side=='S']
            assert len(targets)==1
            assert len(await actor.broker.positions('account'))==1
            first_qty=actor.group.plan.broker_batches[0][0].quantity
            assert targets[0].size==first_qty
            await actor.step(9.68,9.69)
            assert await actor.broker.positions('account')==[]
            sells=[fill for fill in actor.executions if fill.side=='S']
            assert len(sells)==3
            buys=[fill for fill in actor.executions if fill.side=='B']
            assert sum(fill.size for fill in buys)==sum(fill.size for fill in sells)==actor.approved.quantity
            expected=10000.-sum(fill.size*fill.price+fill.commission for fill in buys)+sum(fill.size*fill.price-fill.commission for fill in sells)
            ledger=await actor.broker.account_ledger('account')
            await actor.oms.close()
            await asyncio.sleep(0)
            assert ledger.cashbalance==pytest.approx(expected)
            before=len(actor.executions)
            await actor.step(11.,11.01)
            assert len(actor.executions)==before
            assert [b[1].price for b in actor.group.plan.broker_batches]==[10.3,10.4,10.5]
        finally:
            await actor.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('lost_ack',[False,True])
def test_actual_partial_acquisition_repair_ack_and_broker_cash_recovery(lost_ack):
    from src.trading_runtime.order_management import _apply_cumulative_fill, OrderManagementState
    async def exercise():
        actor=await Actor().start()
        try:
            first=actor.group.plan.broker_batches[0][0].quantity
            wanted=first+6
            actor.at+=timedelta(milliseconds=100)
            event=replace(quote(bid=10.,ask=10.01,ask_size=wanted*4),ticker='AAA',raw={'conid':1},
                          ts=actor.at,ingest_ts=actor.at)
            actor.executions.extend(await actor.broker.on_market_event(event))
            for order in await actor.broker.live_orders():
                identity=str(order.orderId)
                if identity in actor.group.broker_order_roles:
                    _apply_cumulative_fill(actor.group,identity,float(order.filledQuantity),
                                           actor.group.broker_order_roles[identity])
            place=actor.broker.place_orders
            if lost_ack:
                async def incomplete(account_id,orders):
                    actual=await place(account_id,orders)
                    return actual[:1]
                actor.broker.place_orders=incomplete
                with pytest.raises(RuntimeError,match='unique broker acknowledgements'):
                    await actor.oms.reconcile_protection(actor.group)
                assert actor.group.state==OrderManagementState.OUTCOME_UNKNOWN
                actor.broker.place_orders=place
            else:
                result=await actor.oms.reconcile_protection(actor.group)
                actions=[v for v in result['actions'] if v['action']=='place_ladder_repair_pair']
                assert len(actions)==1 and actions[0]['quantity']==6
                assert actions[0]['target_price']==10.4
                assert result['required_quantity']==result['protected_quantity']==wanted
            count=len(await actor.broker.live_orders())
            checkpoint=actor.broker.checkpoint_state()
            ledger=await actor.broker.account_ledger('account')
            restored=SimulatedBrokerAdapter(['account'],SimulationConfig(initial_cash=10000.),
                mode=TradingMode.BACKTEST,initial_time=actor.at)
            await restored.initialize()
            restored.restore_checkpoint_state(checkpoint)
            actor.broker=restored
            assert await actor.broker.account_ledger('account')==ledger
            await actor.recover_oms()
            await actor.oms.reconcile()
            assert actor.group.filled_quantity==wanted
            assert (await actor.oms.reconcile_protection(actor.group))['actions']==[]
            assert len(await actor.broker.live_orders())==count
            assert actor.group.plan.order_slice_ids[-2:]==('lot-2','lot-2')
            assert actor.group.plan.broker_batches[-1][0].price==10.4
            await actor.step(10.,10.01)
            assert actor.group.filled_quantity==actor.approved.quantity
            repairs=[v for v in await actor.broker.live_orders() if 'repair-' in v.cOID]
            assert len(repairs)==2 and all(v.order_status.value=='Cancelled' for v in repairs)
            await actor.step(9.68,9.69)
            assert await actor.broker.positions('account')==[]
            buys=[v for v in actor.executions if v.side=='B']
            sells=[v for v in actor.executions if v.side=='S']
            assert sum(v.size for v in buys)==sum(v.size for v in sells)==actor.approved.quantity
            expected=10000.-sum(v.size*v.price+v.commission for v in buys)+sum(v.size*v.price-v.commission for v in sells)
            assert (await actor.broker.account_ledger('account')).cashbalance==pytest.approx(expected)
        finally:
            await actor.close()
    asyncio.run(exercise())


def test_public_reconcile_repairs_partial_acquisition_without_private_fill_setup():
    async def exercise():
        actor=await Actor().start()
        try:
            wanted=actor.group.plan.broker_batches[0][0].quantity+6
            await actor.step(10.,10.01,ask_size=wanted*4)
            assert actor.group.filled_quantity==wanted
            assert len(actor.group.plan.broker_batches)==4
            assert actor.group.plan.broker_batches[-1][0].quantity==6
            assert actor.group.plan.broker_batches[-1][0].price==10.4
            assert (await actor.oms.reconcile_protection(actor.group))['actions']==[]
            assert actor.group.protection_coverage_quantity==wanted
        finally:
            await actor.close()
    asyncio.run(exercise())


def normalized_checkpoint(actor, client=None, first=None, prior=None, sequence=1, proofs=None):
    from datetime import date
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_oms_projection import freeze_oms_group, oms_group_state_batch
    from src.trading_runtime.arte_journal_writer import publish_typed_batch, load_committed_prefix
    from tests.test_arte_journal_writer import MemoryClient
    if client is None:
        client=MemoryClient()
        first_id=str(uuid4())
        first=strategy_intent_batch(actor.intent,run_id=actor.run_id,run_month=date(2026,8,1),
            account_id='account',attempt_id=str(uuid4()),batch_id=first_id,
            prior_batch_id='00000000-0000-0000-0000-000000000000',sequence=1,
            source_cursor='source',run_status='running',recorded_at=actor.intent.event_time)
        publish_typed_batch(client,first)
        prior=first_id
        sequence=2
    original=next(r.payload for r in actor.journal.records(actor.run_id)
        if r.entity_type=='portfolio_reservation' and r.payload.get('event')=='reservation_created')
    batch=oms_group_state_batch(freeze_oms_group(actor.group),run_id=actor.run_id,
        run_month=first.run_month,attempt_id=first.attempt_id,batch_id=str(uuid4()),
        prior_batch_id=prior,sequence=sequence,source_cursor='oms',run_status='running',
        strategy_id='component-fixture',strategy_revision=1,recorded_at=actor.at,
        published_intent_batch=first,committed_intent_batch_id=first.batch_id,
        admission_source_intent=actor.intent,admission_reservation=original,
        authorized_protection=proofs)
    publish_typed_batch(client,batch)
    return client,first,batch.batch_id,load_committed_prefix(client,actor.run_id)


def test_remaining_ceiling_from_actual_committed_normalized_oms_roster():
    from decimal import Decimal
    from src.trading_runtime.fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
    async def exercise():
        actor=await Actor().start()
        try:
            client,first,prior,prefix=normalized_checkpoint(actor)
            def read():
                return load_fixed_structural_lot_stop_ceiling(client,prefix,entry=actor.entry,
                    intervals=actor.intervals,intent=actor.intent,group_id=actor.group.group_id,
                    strategy_identity=('component-fixture',1))
            pending=read()
            assert pending.ceiling==Decimal('10.3')
            assert pending.acquiring==('lot-1','lot-2','lot-3')
            await actor.step(10.,10.01)
            client,first,prior,prefix=normalized_checkpoint(actor,client,first,prior,3)
            assert read().ceiling==Decimal('10.3')
            await actor.step(10.31,10.32)
            client,first,prior,prefix=normalized_checkpoint(actor,client,first,prior,4)
            after=read()
            assert after.ceiling==Decimal('10.4')
            assert after.remaining[0]==('lot-1',Decimal(0))
            assert after.acquiring==()
            await actor.step(9.68,9.69)
            client,first,prior,prefix=normalized_checkpoint(actor,client,first,prior,5)
            flat=read()
            assert flat.ceiling is None and flat.acquiring==()
            assert all(qty==0 for _,qty in flat.remaining)
            assert [v.profit_target_price for v in actor.intent.protection_profile.slices]==[10.3,10.4,10.5]
        finally:
            await actor.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('change',['target','repair_target','oversold','nan','foreign','duplicate','missing','unknown'])
def test_normalized_ceiling_rejects_mutated_owned_roster(change):
    from src.trading_runtime.fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
    from src.trading_runtime.order_management import OrderManagementState
    async def exercise():
        actor=await Actor().start()
        try:
            if change=='repair_target':
                wanted=actor.group.plan.broker_batches[0][0].quantity+6
                await actor.step(10.,10.01,ask_size=wanted*4)
                actor.group.orders[-2]=replace(actor.group.orders[-2],price=10.3)
            elif change=='target':
                actor.group.orders[4]=replace(actor.group.orders[4],price=10.3)
            elif change=='oversold':
                actor.group.filled_by_broker_order[actor.group.broker_order_ids[1]]=1.
            elif change=='nan':
                actor.group.filled_by_broker_order[actor.group.broker_order_ids[0]]=float('nan')
            elif change=='foreign':
                actor.group.broker_order_slices[actor.group.broker_order_ids[0]]='foreign'
            elif change=='duplicate':
                actor.group.broker_order_ids.append(actor.group.broker_order_ids[0])
            elif change=='missing':
                del actor.group.broker_order_roles[actor.group.broker_order_ids[0]]
            else:
                actor.group.state=OrderManagementState.OUTCOME_UNKNOWN
            stage='projection'
            with pytest.raises((ValueError,RuntimeError)):
                client,_,_,prefix=normalized_checkpoint(actor)
                stage='ceiling'
                load_fixed_structural_lot_stop_ceiling(client,prefix,entry=actor.entry,
                    intervals=actor.intervals,intent=actor.intent,group_id=actor.group.group_id,
                    strategy_identity=('component-fixture',1))
            if change in ('oversold','unknown'):
                assert stage=='ceiling'
            if change in ('target','repair_target'):
                assert stage=='projection'
        finally:
            await actor.close()
    asyncio.run(exercise())


def test_public_remaining_stop_ack_passes_sold_target_without_moving_fixed_targets():
    """Actual generic component command; no installed management declaration."""
    async def exercise():
        actor=await Actor().start()
        try:
            await actor.step(10.,10.01)
            await actor.step(10.31,10.32)
            await actor.step(10.38,10.39)
            from src.trading_runtime.signals import StrategyIntent
            # Explicit fixture command. The future source owner must derive
            # this from the selected transition and journal its typed witness.
            command=StrategyIntent(intent_id=str(uuid4()),ticker='AAA',event_time=actor.at,
                action='replace_protective_stop',quantity=sum(float(v.position) for v in await actor.broker.positions('account')),
                reference_price=10.38,
                invalidation_price=10.32,reason='component-fixed-lot-stop')
            actor.journal.append(run_id=actor.run_id,category='strategy',entity_type='strategy_intent',
                entity_id=command.intent_id,account_id='account',event_time=actor.at,payload=command.payload())
            decision,approved=await actor.portfolio.approve(command,account_id='account',assignment_id=actor.entry.proposal.assignment_id)
            assert approved is not None,decision.reasons
            await actor.oms.submit_intent(approved,account_id='account',event=None)
            from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
            live=await actor.broker.live_orders()
            stops=[v for v in live if v.orderType=='STP' and v.order_status in OPEN_ORDER_STATUSES]
            assert len(stops)==2 and all(v.auxPrice==10.32 for v in stops)
            assert [b[1].price for b in actor.group.plan.broker_batches[:3]]==[10.3,10.4,10.5]
            original_ids={v.orderId for v in stops}
            checkpoint=actor.broker.checkpoint_state()
            await actor.oms.close()
            await asyncio.sleep(0)
            broker=SimulatedBrokerAdapter(['account'],SimulationConfig(initial_cash=10000.),
                mode=TradingMode.BACKTEST,initial_time=actor.at)
            await broker.initialize()
            broker.restore_checkpoint_state(checkpoint)
            actor.broker=broker
            await actor.recover_oms()
            restored=[v for v in await broker.live_orders() if v.orderType=='STP' and v.order_status in OPEN_ORDER_STATUSES]
            assert {v.orderId for v in restored}==original_ids
            assert all(v.auxPrice==10.32 for v in restored)
            assert [v.profit_target_price for v in actor.group.intent.protection_profile.slices]==[10.3,10.4,10.5]
        finally:
            await actor.close()
    asyncio.run(exercise())
