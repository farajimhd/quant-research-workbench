"""Common actor ACK/failure and normalized cold source controls."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_fixed_structural_lot_actor import Actor


async def command(actor, desired=10.32):
    from src.trading_runtime.signals import StrategyIntent
    request = StrategyIntent(intent_id=str(uuid4()), ticker='AAA', event_time=actor.at,
        action='replace_protective_stop', quantity=sum(float(row.position) for row in await actor.broker.positions('account')),
        reference_price=max(10.38, desired + .01), invalidation_price=desired, reason='component-fixed-lot-stop')
    actor.journal.append(run_id=actor.run_id, category='strategy', entity_type='strategy_intent',
        entity_id=request.intent_id, account_id='account', event_time=actor.at, payload=request.payload())
    decision, approved = await actor.portfolio.approve(request, account_id='account',
        assignment_id=actor.entry.proposal.assignment_id)
    assert approved is not None, decision.reasons
    return approved


async def acquired():
    actor = await Actor().start()
    await actor.step(10., 10.01)
    await actor.step(10.31, 10.32)
    await actor.step(10.38, 10.39)
    return actor


def test_later_active_target_rejects_before_any_broker_side_effect():
    async def run():
        actor = await acquired()
        try:
            calls = []
            original = actor.broker.modify_order
            async def capture(*args, **kwargs):
                calls.append(args)
                return await original(*args, **kwargs)
            actor.broker.modify_order = capture
            with pytest.raises(ValueError, match='active|bracket|target'):
                await actor.oms.submit_intent(await command(actor, 10.45), account_id='account', event=None)
            assert calls == []
            assert [item.stop.price for item in actor.group.intent.protection_profile.slices] == [9.69]*3
        finally:
            await actor.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['cancelled','insufficient','nan_reference','mixed'])
def test_all_groups_and_later_lot_coverage_validate_before_modify(change):
    async def run():
        actor = await acquired()
        try:
            from src.trading_runtime.ibkr_schema import OrderStatus
            calls=[]
            original=actor.broker.modify_order
            async def capture(*args, **kwargs):
                calls.append(args)
                return await original(*args, **kwargs)
            actor.broker.modify_order=capture
            approved=await command(actor)
            if change == 'nan_reference':
                approved=replace(approved,reference_price=float('nan'))
            elif change == 'mixed':
                from copy import copy
                other=copy(actor.group)
                other.group_id='other-group'
                other.intent=replace(other.intent,protection_profile=None)
                actor.oms._groups[other.group_id]=other
            else:
                stop=next(row for row in await actor.broker.live_orders()
                    if row.orderType=='STP' and actor.group.broker_order_slices[str(row.orderId)]=='lot-3')
                if change == 'cancelled':
                    await actor.broker.cancel_order('account',str(stop.orderId))
                else:
                    # Controlled authoritative broker snapshot for quantity loss.
                    original_live=actor.broker.live_orders
                    async def reduced():
                        return [replace(row,remainingQuantity=1.) if row.orderId==stop.orderId else row
                                for row in await original_live()]
                    actor.broker.live_orders=reduced
            with pytest.raises(ValueError):
                await actor.oms.submit_intent(approved,account_id='account',event=None)
            assert calls==[]
        finally:
            await actor.close()
    asyncio.run(run())


def test_confirmed_duplicate_is_noop_and_returns_actual_protected_group():
    async def run():
        from copy import copy
        from src.trading_runtime.independent_lot_stop_amendment import replace_independent_stops
        actor=await acquired()
        try:
            approved=await command(actor)
            await actor.oms.submit_intent(approved,account_id='account',event=None)
            before=len(actor.journal.records(actor.run_id))
            original=actor.broker.modify_order
            async def reject(*args, **kwargs):
                raise AssertionError('duplicate must not modify')
            actor.broker.modify_order=reject
            flat=copy(actor.group)
            flat.group_id='flat-group'
            flat.orders=[]
            flat.broker_order_ids=[]
            flat.broker_order_request_indexes={}
            flat.broker_order_roles={}
            flat.broker_order_slices={}
            flat.plan=replace(flat.plan,orders=(),batches=(),order_slice_ids=())
            actor.oms._groups[flat.group_id]=flat
            result=await replace_independent_stops(actor.oms,approved,'account')
            assert result.group_id==actor.group.group_id
            assert len(actor.journal.records(actor.run_id))==before
        finally:
            await actor.close()
    asyncio.run(run())


def test_repair_after_earned_stop_has_creation_proof_and_fresh_cold_profile():
    async def run():
        actor=await acquired()
        try:
            await actor.oms.submit_intent(await command(actor),account_id='account',event=None)
            for row in await actor.broker.live_orders():
                if (actor.group.broker_order_slices[str(row.orderId)]=='lot-2'
                        and actor.group.broker_order_roles[str(row.orderId)]!='entry'):
                    await actor.broker.cancel_order('account',str(row.orderId))
            await actor.oms.reconcile()
            repairs=[row for row in actor.group.orders if 'repair-' in row.cOID]
            assert len(repairs)==2
            assert next(row for row in repairs if row.orderType=='STP').auxPrice==10.32
            record=next(row for row in reversed(actor.journal.records(actor.run_id))
                if row.entity_type=='order_group_state' and row.entity_id==actor.group.group_id)
            proofs=actor.journal.oms_effective_protection_for_record(record)
            assert any(key.startswith('initial_stop:') for key in proofs)
            # Reconstruct this exact original group record against its complete
            # earlier per-order proof history. Full normalized contiguous
            # transport is independently covered by the native-prefix actor test;
            # this component must not fabricate a sequence1->group graft.
            from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState,_approved_strategy_one_oms_intent
            from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
            bindings=tuple(dict(broker_order_id=order_id,
                request_index=actor.group.broker_order_request_indexes[order_id],
                slice_id=actor.group.broker_order_slices[order_id],role=actor.group.broker_order_roles[order_id])
                for order_id in actor.group.broker_order_ids)
            state=RecoveredOmsGroupState(record.sequence,actor.intent.intent_id,
                dict(account_id='account',group_id=actor.group.group_id,strategy_revision=1,
                     updated_at=record.event_time.strftime('%Y-%m-%d %H:%M:%S.%f')),
                tuple(actor.group.orders),tuple(0 for _ in actor.group.orders),
                actor.group.plan.order_slice_ids,bindings,(),())
            history=CompleteProtectionHistory(actor.run_id,record.sequence,(str(uuid4()),),
                tuple(r for r in actor.journal.protection_records(actor.run_id) if r.sequence<record.sequence))
            restored,_=_approved_strategy_one_oms_intent(state,SimpleNamespace(intent=actor.intent),history,None,None)
            assert [row.stop.price for row in restored.protection_profile.slices]==[9.69,10.32,10.32]
            await actor.recover_oms()
            assert [row.stop.price for row in actor.group.intent.protection_profile.slices]==[9.69,10.32,10.32]
            assert next(row for row in actor.group.orders if row.orderType=='STP' and 'repair-' in row.cOID).auxPrice==10.32
        finally:
            await actor.close()
    asyncio.run(run())


@pytest.mark.parametrize('lost_after_ack', [False, True])
def test_partial_multi_lot_failure_retains_actual_ack_and_explicit_reconciliation(lost_after_ack):
    async def run():
        from src.trading_runtime.independent_lot_stop_amendment import replace_independent_stops
        from src.trading_runtime.order_management import OrderManagementState
        actor = await acquired()
        try:
            calls = []
            original = actor.broker.modify_order
            async def interrupted(*args, **kwargs):
                calls.append(args[1])
                if len(calls) == 2:
                    if lost_after_ack:
                        await original(*args, **kwargs)
                    raise RuntimeError('controlled transport loss')
                return await original(*args, **kwargs)
            actor.broker.modify_order = interrupted
            approved = await command(actor)
            with pytest.raises(RuntimeError, match='transport loss'):
                await actor.oms.submit_intent(approved, account_id='account', event=None)
            assert actor.group.state == OrderManagementState.OUTCOME_UNKNOWN
            assert [item.stop.price for item in actor.group.intent.protection_profile.slices] == [9.69,10.32,9.69]
            assert len(calls) == 2
            # Explicit exact-command reconciliation; never automatic retry.
            actor.broker.modify_order = original
            await replace_independent_stops(actor.oms, approved, 'account')
            assert [item.stop.price for item in actor.group.intent.protection_profile.slices] == [9.69,10.32,10.32]
            assert [item.profit_target_price for item in actor.group.intent.protection_profile.slices] == [10.3,10.4,10.5]
            assert len(await actor.broker.live_orders()) == 9
        finally:
            await actor.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', [None,'future_clock','future_sequence','foreign_client','foreign_lot','price','missing','target'])
def test_cold_profile_requires_per_order_effective_source_and_group_clock(change):
    async def run():
        from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState, _approved_strategy_one_oms_intent
        from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
        actor = await acquired()
        try:
            await actor.oms.submit_intent(await command(actor), account_id='account', event=None)
            history = tuple(actor.journal.protection_records(actor.run_id))
            seq = max(record.sequence for record in actor.journal.records(actor.run_id)) + 1
            bindings = tuple(dict(broker_order_id=order_id,
                request_index=actor.group.broker_order_request_indexes[order_id],
                slice_id=actor.group.broker_order_slices[order_id], role=actor.group.broker_order_roles[order_id])
                for order_id in actor.group.broker_order_ids)
            state = RecoveredOmsGroupState(seq,actor.intent.intent_id,
                dict(account_id='account',group_id=actor.group.group_id,strategy_revision=1,
                     updated_at=actor.at.strftime('%Y-%m-%d %H:%M:%S.%f')),
                tuple(actor.group.orders),tuple(0 for _ in actor.group.orders),
                actor.group.plan.order_slice_ids,bindings,(),())
            effective = next(record for record in history if record.payload.get('action') == 'replace_protective_stop'
                and record.payload.get('phase') == 'effective')
            if change:
                from datetime import timedelta
                if change == 'future_clock':
                    changed = replace(effective,event_time=actor.at+timedelta(milliseconds=1))
                elif change == 'future_sequence':
                    changed = replace(effective,sequence=seq)
                elif change == 'missing':
                    changed = None
                else:
                    payload = dict(effective.payload)
                    if change == 'foreign_client': payload['client_order_id']='foreign'
                    if change == 'price': payload['price']=10.33
                    if change == 'target': payload.update(kind='target',action='replace_profit_target')
                    changed = replace(effective,payload=payload)
                    if change == 'foreign_lot':
                        state = replace(state,broker_bindings=({**bindings[0],'slice_id':'foreign'},*bindings[1:]))
                history = tuple(changed if record is effective else record for record in history if record is not effective or changed is not None)
            authority = CompleteProtectionHistory(actor.run_id,seq,(str(uuid4()),),history)
            if change:
                with pytest.raises(ValueError):
                    _approved_strategy_one_oms_intent(state,SimpleNamespace(intent=actor.intent),authority,None,None)
            else:
                restored,_ = _approved_strategy_one_oms_intent(state,SimpleNamespace(intent=actor.intent),authority,None,None)
                assert [item.stop.price for item in restored.protection_profile.slices] == [9.69,10.32,10.32]
                assert [item.profit_target_price for item in restored.protection_profile.slices] == [10.3,10.4,10.5]
        finally:
            await actor.close()
    asyncio.run(run())


@pytest.mark.parametrize('observed_stop',[9.60,10.01])
def test_changed_broker_stop_is_observation_not_initial_creation(observed_stop):
    async def run():
        actor=await acquired()
        try:
            stop=next(row for row in await actor.broker.live_orders()
                if row.orderType=='STP' and actor.group.broker_order_slices[str(row.orderId)]=='lot-3')
            index=actor.group.broker_order_request_indexes[str(stop.orderId)]
            original=actor.group.orders[index]
            matching=tuple(r for r in actor.journal.protection_records(actor.run_id)
                if r.payload.get('order_id')==str(stop.orderId) and r.payload.get('phase')=='effective')
            assert matching and all(r.payload['price']==original.auxPrice for r in matching)
            before=actor.journal.latest_sequence(actor.run_id)
            await actor.broker.modify_order('account',str(stop.orderId),replace(original,auxPrice=observed_stop))
            await actor.oms.reconcile()
            records=actor.journal.records(actor.run_id,after_sequence=before)
            assert any(r.entity_type=='order_group_state' and r.payload.get('event')=='broker_order_update'
                for r in records)
            assert not any(r.entity_type=='protection_change' and r.payload.get('order_id')==str(stop.orderId)
                and r.payload.get('phase')=='effective' for r in records)
            assert actor.group.orders[index].auxPrice==original.auxPrice
            # Restore the real broker to its source-bound request, then cancel.
            # Matching-price terminal protection is still audited as inactive.
            await actor.broker.modify_order('account',str(stop.orderId),original)
            await actor.oms.reconcile()
            before=actor.journal.latest_sequence(actor.run_id)
            await actor.broker.cancel_order('account',str(stop.orderId))
            await actor.oms.reconcile()
            records=actor.journal.records(actor.run_id,after_sequence=before)
            assert any(r.entity_type=='protection_change' and r.payload.get('order_id')==str(stop.orderId)
                and r.payload.get('phase')=='effective' and not r.payload['active'] for r in records)
        finally:await actor.close()
    asyncio.run(run())
