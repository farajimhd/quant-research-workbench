"""Selected real-roster protection and pure schema; no installed admission."""
import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from src.trading_runtime.strategy_one_position import (
    ProtectionState, ProtectionTransition, ResistanceBreak,
    advance_protection, confirm_protection_transition,
)
from src.trading_runtime.fixed_structural_lot_state import (
    FixedStructuralLotProtectionTransition, open_fixed_structural_lot_protection,
    advance_fixed_structural_lot_state, confirm_fixed_structural_lot_state,
)
from src.trading_runtime.fixed_structural_lot_snapshot import (
    project_fixed_structural_lot_snapshot,restore_fixed_structural_lot_snapshot,_digest,
)
from tests.test_fixed_structural_lot_actor import Actor,normalized_checkpoint
from tests.test_strategy_one_position import opening,level


def arguments(**changes):
    values=dict(now_ms=31000,bid=10.38,ask=10.39,tick=.01,
        low_boundary_ms=None,low_int=None,low_price_valid=False,low_extremes_valid=False,
        breaks=tuple(ResistanceBreak(31000,level(i,center)) for i,center in enumerate((10.34,10.35,10.36))),
        overhead_levels=(),price_bearing_bar=True,allows_completed_30s_trailing=False)
    return {**values,**changes}


def test_selected_stop_passes_retired_original_target_without_target_amendment_and_cold_roundtrips():
    async def exercise():
        actor=await Actor().start()
        try:
            await actor.step(10.,10.01)
            client,first,prior,prefix=normalized_checkpoint(actor)
            owner=dict(client=client,prefix=prefix,intervals=actor.intervals,intent=actor.intent,
                       strategy_identity=('component-fixture',1))
            previous=open_fixed_structural_lot_protection(actor.entry,group_id=actor.group.group_id,**owner)
            # The first target is still held: the initial ceiling still clamps.
            clamped=advance_fixed_structural_lot_state(previous,**owner,**arguments())
            assert clamped.proposed.protection.stop<10.3
            await actor.step(10.31,10.32)
            client,first,prior,prefix=normalized_checkpoint(actor,client,first,prior,3)
            owner['prefix']=prefix
            proposal=advance_fixed_structural_lot_state(previous,**owner,**arguments())
            assert 10.3<proposal.proposed.protection.stop<10.4
            assert proposal.proposed.protection.target==10.3
            assert proposal.transition.target_amendment is None
            refused=confirm_fixed_structural_lot_state(proposal,stop_confirmed=False,**owner)
            assert refused.protection.stop==previous.protection.stop
            assert refused.protection.applied_groups==previous.protection.applied_groups==0
            assert refused.protection.earned_groups==1 and len(refused.protection.accepted_ids)==3
            accepted=confirm_fixed_structural_lot_state(proposal,stop_confirmed=True,**owner)
            assert accepted.protection.applied_groups==1
            assert accepted.protection.stop==proposal.proposed.protection.stop
            assert accepted.protection.target==previous.protection.target==10.3
            rows=project_fixed_structural_lot_snapshot(accepted,**owner)
            restored=restore_fixed_structural_lot_snapshot(rows,entry=actor.entry,**owner)
            assert restored==accepted
            assert rows.root['original_target']=='10.300000000000000000'
            assert rows.root['ceiling']=='10.400000000000000000'
            assert [v['fixed_target'] for v in rows.lots]==[
                '10.300000000000000000','10.400000000000000000','10.500000000000000000']
            with pytest.raises(ValueError,match='targets'):
                confirm_fixed_structural_lot_state(proposal,stop_confirmed=True,target_confirmed=True,**owner)
        finally:
            await actor.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('field,value',[('target',float('nan')),('target',float('inf')),
 ('target',-1.),('stop',float('nan')),('stop',float('inf'))])
def test_selected_pure_ceiling_rejects_malformed_original_geometry(field,value):
    state=replace(opening().state,**{field:value})
    with pytest.raises(ValueError):
        advance_protection(state,stop_ceiling=10.4,allows_target_escalation=False,**arguments())
    with pytest.raises(ValueError):
        confirm_protection_transition(state,ProtectionTransition(replace(state,boundary_ms=31000)),
                                      stop_ceiling=10.4,target_confirmed=False,stop_confirmed=False)


@pytest.mark.parametrize('value',[None,object(),{},ProtectionState(31000,9.69,10.3)])
def test_selected_confirmation_rejects_malformed_transition_deliberately(value):
    with pytest.raises(ValueError):
        confirm_protection_transition(opening().state,value,stop_ceiling=10.4,
                                      target_confirmed=False,stop_confirmed=False)
    with pytest.raises(ValueError):
        FixedStructuralLotProtectionTransition(value,value,value)


@pytest.mark.parametrize('ceiling',[True,10,Decimal('10.4'),float('nan'),float('inf'),10.2])
def test_explicit_ceiling_exact_type_and_fixed_target_requirement(ceiling):
    with pytest.raises(ValueError):
        advance_protection(opening().state,stop_ceiling=ceiling,allows_target_escalation=False,**arguments())


def test_ceiling_cannot_enable_target_escalation_or_target_ack():
    state=opening().state
    with pytest.raises(ValueError):
        advance_protection(state,stop_ceiling=10.4,**arguments())
    proposal=ProtectionTransition(replace(state,boundary_ms=31000,target=10.4),target_amendment={'price':10.4})
    with pytest.raises(ValueError):
        confirm_protection_transition(state,proposal,stop_ceiling=10.4,target_confirmed=False,stop_confirmed=False)


@pytest.mark.parametrize('change',['ceiling','original_target','lot_target','weight','foreign_source',
 'future_roster','duplicate','omitted','reordered','scalar_alias'])
def test_cold_snapshot_rejects_resealed_source_and_roster_drift(change):
    async def exercise():
        actor=await Actor().start()
        try:
            await actor.step(10.,10.01)
            client,_,_,prefix=normalized_checkpoint(actor)
            owner=dict(client=client,prefix=prefix,intervals=actor.intervals,intent=actor.intent,
                       strategy_identity=('component-fixture',1))
            state=open_fixed_structural_lot_protection(actor.entry,group_id=actor.group.group_id,**owner)
            rows=project_fixed_structural_lot_snapshot(state,**owner)
            root=dict(rows.root)
            lots=tuple(dict(v) for v in rows.lots)
            if change=='ceiling': root['ceiling']='10.400000000000000000'
            elif change=='original_target': root['original_target']='10.400000000000000000'
            elif change=='lot_target': lots[0]['fixed_target']='10.400000000000000000'
            elif change=='weight': lots[0]['weight_denominator']=4
            elif change=='foreign_source': root['entry_source_hash']='f'*64
            elif change=='future_roster': root['through_sequence']+=1
            elif change=='duplicate': lots=(*lots,lots[0]); root['lot_count']+=1
            elif change=='omitted': lots=lots[:-1]; root['lot_count']-=1
            elif change=='reordered': lots=tuple(reversed(lots))
            else: root['through_sequence']=float(root['through_sequence'])
            root['children_hash']=_digest([lots,rows.resistances])
            root['content_hash']=_digest({k:v for k,v in root.items() if k!='content_hash'})
            mutant=replace(rows,root=root,lots=lots)
            with pytest.raises(ValueError):
                restore_fixed_structural_lot_snapshot(mutant,entry=actor.entry,**owner)
        finally:
            await actor.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('change',['duplicate_prior','foreign_prior','resistance_order','foreign_child_root'])
def test_cold_snapshot_rejects_resealed_prior_resistance_and_parent_mismatch(change):
    async def exercise():
        actor=await Actor().start()
        try:
            await actor.step(10.,10.01)
            await actor.step(10.31,10.32)
            client,_,_,prefix=normalized_checkpoint(actor)
            owner=dict(client=client,prefix=prefix,intervals=actor.intervals,intent=actor.intent,
                       strategy_identity=('component-fixture',1))
            state=open_fixed_structural_lot_protection(actor.entry,group_id=actor.group.group_id,**owner)
            first=advance_fixed_structural_lot_state(state,**owner,**arguments(breaks=tuple(
                ResistanceBreak(31000,level(i,c)) for i,c in enumerate((9.8,9.9,10.1)))))
            state=confirm_fixed_structural_lot_state(first,stop_confirmed=True,**owner)
            second=advance_fixed_structural_lot_state(state,**owner,**arguments(now_ms=32000,breaks=tuple(
                ResistanceBreak(32000,level('new-'+str(i),c)) for i,c in enumerate((10.34,10.35,10.36)))))
            state=confirm_fixed_structural_lot_state(second,stop_confirmed=True,**owner)
            rows=project_fixed_structural_lot_snapshot(state,**owner)
            children=tuple(dict(v) for v in rows.resistances)
            assert len(children)==6 and [v['role'] for v in children]==['prior']*3+['earned']*3
            if change=='duplicate_prior': children[1]['level_id']=children[0]['level_id']
            elif change=='foreign_prior': children[0]['level_id']='foreign'
            elif change=='resistance_order': children=tuple(reversed(children))
            else: children[0]['run_id']='foreign'
            root=dict(rows.root)
            root['children_hash']=_digest([rows.lots,children])
            root['content_hash']=_digest({k:v for k,v in root.items() if k!='content_hash'})
            with pytest.raises(ValueError):
                restore_fixed_structural_lot_snapshot(replace(rows,root=root,resistances=children),
                                                     entry=actor.entry,**owner)
        finally:
            await actor.close()
    asyncio.run(exercise())


def test_confirmation_does_not_use_later_retirement_as_earlier_ceiling_authority():
    from datetime import timedelta
    async def exercise():
        actor=await Actor().start()
        try:
            await actor.step(10.,10.01)
            await actor.step(10.31,10.32)
            client,first,prior,prefix=normalized_checkpoint(actor)
            owner=dict(client=client,prefix=prefix,intervals=actor.intervals,intent=actor.intent,
                       strategy_identity=('component-fixture',1))
            previous=open_fixed_structural_lot_protection(actor.entry,group_id=actor.group.group_id,**owner)
            proposal=advance_fixed_structural_lot_state(previous,**owner,**arguments())
            actor.at=actor.intent.event_time+timedelta(milliseconds=1000)
            await actor.step(10.41,10.42)
            client,first,prior,prefix=normalized_checkpoint(actor,client,first,prior,3)
            owner['prefix']=prefix
            with pytest.raises(ValueError,match='future'):
                confirm_fixed_structural_lot_state(proposal,stop_confirmed=True,**owner)
            assert proposal.previous.protection.stop==actor.entry.proposal.initial_stop
        finally:
            await actor.close()
    asyncio.run(exercise())
