"""Actual own packets; no installed admission or durable financial authority."""
from dataclasses import replace
from uuid import uuid4

import pytest

from test_arte_declared_native_command_v4 import case
from test_arte_declared_native_management_v4 import packet as management_packet
from src.trading_runtime import arte_declared_native_v4_unit as module
from src.trading_runtime import arte_declared_native_command_v4 as entry
from src.trading_runtime import arte_declared_native_management_v4 as management


def test_actual_entry_packet_keeps_original_scalar_rows_and_stable_base(case):
    unit=module.prepare_declared_native_v4_unit(case.packet,**case.args,predecessor=case.predecessor)
    original=unit.base
    assert entry._exact(unit.base,case.packet.base)
    for _ in range(3): unit.__post_init__(); assert unit.base is original
    assert unit.packet is case.packet
    assert unit.base.events[0]['entity_type']=='declared_native_intent'
    assert entry._exact(unit.base.intents,case.packet.base.intents)
    assert entry._exact(unit.base.intent_slices,case.packet.base.intent_slices)


def test_actual_management_branches_replay_complete_original_requests(management_packet):
    p=management_packet
    unit=module.prepare_declared_native_v4_unit(p.packet,**p.args)
    restored=management.readback_declared_management_transport(p.packet,**p.args)
    assert entry._exact(restored.intents,p.submission.intents)
    assert unit.base.first_sequence==p.records[0].sequence
    assert unit.base.last_sequence==p.records[-1].sequence
    assert tuple(r['record_id'] for r in unit.base.events)==tuple(r.record_id for r in p.records)
    assert tuple(r['intent_id'] for r in unit.base.intents)==tuple(i.intent_id for i in p.submission.intents)
    assert all(r['entity_type']=='declared_native_management_intent' for r in unit.base.events)
    original=unit.base; unit.__post_init__(); assert unit.base is original


def two_fragments(p):
    """Structural fixture only: two real originals, not one approved command."""
    first=p.packet.bases[0]
    # A distinct original stop/exit request from the actual entry packet. Own
    # source replay must reject this mixture; only contiguous transport is tested.
    original=p.case.packet.base
    event=dict(original.events[0],entity_type='declared_native_management_intent',
               sequence=first.last_sequence+1,batch_id=first.batch_id)
    intents=tuple(dict(r,batch_id=first.batch_id) for r in original.intents)
    slices=tuple(dict(r,batch_id=first.batch_id) for r in original.intent_slices)
    second=replace(first,first_sequence=first.last_sequence+1,last_sequence=first.last_sequence+1,
                   events=(event,),intents=intents,intent_slices=slices)
    return replace(p.packet,bases=(first,second))


@pytest.mark.parametrize('management_packet',['protection'],indirect=True)
def test_structural_two_fragment_order_no_rekey_no_discard_and_source_rejects(management_packet):
    packet=two_fragments(management_packet)
    unit=module.DeclaredNativeV4Unit(packet)
    for name in ('events','intents','intent_slices'):
        assert entry._exact(getattr(unit.base,name),tuple(r for b in packet.bases for r in getattr(b,name)))
    assert unit.base.prior_batch_id==packet.bases[0].prior_batch_id==packet.bases[1].prior_batch_id
    with pytest.raises(ValueError): module.prepare_declared_native_v4_unit(packet,**management_packet.args)
    with pytest.raises(ValueError): module.DeclaredNativeV4Unit(replace(packet,bases=tuple(reversed(packet.bases))))


@pytest.mark.parametrize('field,value',[
    ('run_id',str(uuid4())),('batch_id',str(uuid4())),('attempt_id',str(uuid4())),
    ('prior_batch_id',str(uuid4())),('source_cursor','foreign'),('first_sequence',True),
    ('last_sequence',99),('status','completed')])
def test_foreign_fragment_header_or_contiguity_rejects(case,field,value):
    bad=replace(case.packet,base=replace(case.packet.base,**{field:value}))
    with pytest.raises(ValueError): module.DeclaredNativeV4Unit(bad)


@pytest.mark.parametrize('field,value',[('entity_type','strategy_intent'),('correlation_id',str(uuid4())),
    ('causation_id',str(uuid4())),('sequence',8.),('attempt_id',str(uuid4())),('account_id','foreign')])
def test_original_event_link_cannot_drift(case,field,value):
    base=replace(case.packet.base,events=(dict(case.packet.base.events[0],**{field:value}),))
    with pytest.raises(ValueError): module.DeclaredNativeV4Unit(replace(case.packet,base=base))


@pytest.mark.parametrize('mutation',['omission','foreign','duplicate','parent','batch','order'])
def test_complete_entry_companion_inventory_and_links(case,mutation):
    families=[(n,list(r)) for n,r in case.packet.families]
    if mutation=='omission': families[4][1].pop()
    elif mutation=='foreign': families[0]=(management.CONTEXT.name,families[0][1])
    elif mutation=='duplicate': families[4][1][1]=families[4][1][0]
    elif mutation=='order': families[4][1].reverse()
    else:
        field='parent_record_id' if mutation=='parent' else 'batch_id'
        row=dict(families[4][1][0]); row.pop('content_hash'); row[field]=str(uuid4())
        families[4][1][0]=entry._seal(entry.TABLES[4],row)
    with pytest.raises(ValueError):
        module.DeclaredNativeV4Unit(entry.DeclaredEntryRows(case.packet.base,tuple((n,tuple(r)) for n,r in families)))


def test_source_configuration_is_not_approved_by_structural_wrapper(case):
    families=[(n,list(r)) for n,r in case.packet.families]
    row=dict(families[0][1][0]); row.pop('content_hash'); row['configuration_hash']='f'*64
    families[0][1][0]=entry._seal(entry.ENTRY,row)
    packet=entry.DeclaredEntryRows(case.packet.base,tuple((n,tuple(r)) for n,r in families))
    module.DeclaredNativeV4Unit(packet)  # structure never confers source authority
    with pytest.raises(ValueError): module.prepare_declared_native_v4_unit(packet,**case.args,predecessor=case.predecessor)


@pytest.mark.parametrize('field,value',[('revision',9018),('strategy_id','foreign')])
def test_root_own_identity_cannot_conflict_with_original_request(case,field,value):
    families=[(n,list(r)) for n,r in case.packet.families]
    row=dict(families[0][1][0]); row.pop('content_hash'); row[field]=value
    families[0][1][0]=entry._seal(entry.ENTRY,row)
    packet=entry.DeclaredEntryRows(case.packet.base,tuple((n,tuple(r)) for n,r in families))
    with pytest.raises(ValueError,match='identity'): module.DeclaredNativeV4Unit(packet)


def test_cached_base_mutation_or_unexpected_populated_family_rejects(case):
    unit=module.DeclaredNativeV4Unit(case.packet)
    object.__setattr__(unit,'base',replace(unit.base,source_cursor='foreign'))
    with pytest.raises(ValueError,match='cached'): unit.__post_init__()
    base=replace(case.packet.base,signals=(dict(case.packet.base.events[0]),))
    with pytest.raises(ValueError,match='unexpected'): module.DeclaredNativeV4Unit(replace(case.packet,base=base))


def test_foreign_wrapper_types_reject():
    with pytest.raises(ValueError): module.DeclaredNativeV4Unit(object())


@pytest.mark.parametrize('management_packet',['exit'],indirect=True)
@pytest.mark.parametrize('field,value',[('revision',9018),('configuration_hash','f'*64),('source_token','f'*64)])
def test_management_claim_requires_fresh_full_source_and_original_entry(management_packet,field,value):
    p=management_packet
    families=[(n,list(r)) for n,r in p.packet.families]
    row=dict(families[0][1][0]); row.pop('content_hash'); row[field]=value
    families[0][1][0]=entry._seal(management.CONTEXT,row)
    packet=management.DeclaredManagementRows(p.packet.bases,tuple((n,tuple(r)) for n,r in families))
    module.DeclaredNativeV4Unit(packet)  # no hidden source/financial authority
    with pytest.raises(ValueError): module.prepare_declared_native_v4_unit(packet,**p.args)


@pytest.mark.parametrize('mutation',('slice_account','slice_ordinal','slice_count','slice_duplicate','intent_ticker','intent_identity'))
def test_original_request_and_protection_coverage_is_exact(case,mutation):
    base=case.packet.base
    if mutation.startswith('slice'):
        assert len(base.intent_slices)==1
        if mutation=='slice_count':
            base=replace(base,intents=(dict(base.intents[0],protection_slice_count=2),))
        elif mutation=='slice_duplicate': base=replace(base,intent_slices=base.intent_slices*2)
        else:
            key,value=('account_id','foreign') if mutation=='slice_account' else ('ordinal',False)
            base=replace(base,intent_slices=(dict(base.intent_slices[0],**{key:value}),))
    else:
        key,value=('ticker','foreign') if mutation=='intent_ticker' else ('intent_id',str(uuid4()))
        base=replace(base,intents=(dict(base.intents[0],**{key:value}),))
    with pytest.raises(ValueError): module.DeclaredNativeV4Unit(replace(case.packet,base=base))
