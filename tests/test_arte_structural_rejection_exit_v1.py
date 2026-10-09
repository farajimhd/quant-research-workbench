"""Own exit companion; controlled persisted financial readers, no orders."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from test_profit_armed_structural_rejection_confirmation import published
from test_profit_armed_structural_rejection_publication import BATCH
from src.trading_runtime.profit_armed_structural_rejection_confirmation import confirm_structural_rejection_exits
from src.trading_runtime.profit_armed_structural_rejection_exit import structural_rejection_exit_intent
from src.trading_runtime.arte_structural_rejection_exit_v1 import (
    EXIT, prepare_structural_rejection_exit, require_prepared_structural_rejection_exit)

PARENT='00000000-0000-0000-0000-000000000031'


def prepared(monkeypatch):
    client,session,context,capture,requests,*_=published(monkeypatch)
    confirmed=confirm_structural_rejection_exits(client,session,context,capture,requests)[0]
    intent=structural_rejection_exit_intent(confirmed)
    return prepare_structural_rejection_exit(confirmed,intent,parent_record_id=PARENT,
        batch_id=BATCH),confirmed,intent,context


def test_complete_own_schema_links_exact_intent_and_four_financial_roots(monkeypatch):
    evidence,confirmed,intent,context=prepared(monkeypatch)
    assert require_prepared_structural_rejection_exit(evidence) is evidence
    row=evidence.row
    assert set(row)=={name for name,_ in EXIT.columns}
    assert row['intent_id']==intent.intent_id and row['parent_record_id']==PARENT
    assert row['source_manager_checkpoint_sequence']==9
    assert row['source_manager_snapshot_hash']==confirmed.manager_snapshot_hash
    assert row['source_broker_snapshot_hash']=='e'*64
    assert row['source_oms_snapshot_hash']=='f'*64
    assert row['source_portfolio_state_hash']=='d'*64
    assert row['position_quantity']==10. and row['reference_bid_int']==104000
    assert row['strategy_number']==context.profile.owner.manager.contract.strategy_number
    again=prepare_structural_rejection_exit(confirmed,intent,parent_record_id=PARENT,batch_id=BATCH)
    assert dict(row)==dict(again.row)
    with pytest.raises(TypeError): row['position_quantity']=100.
    assert context.profile.owner.manager.runtime.calls==['entry']


@pytest.mark.parametrize('kind',('copy','row','intent','request','completed'))
def test_prepared_evidence_cannot_be_forged_or_outlive_pending_facts(monkeypatch,kind):
    evidence,confirmed,intent,context=prepared(monkeypatch)
    if kind=='copy': evidence=replace(evidence)
    elif kind=='row': object.__setattr__(evidence,'row',MappingProxyType(dict(evidence.row)))
    elif kind=='intent': object.__setattr__(intent,'quantity',100.)
    elif kind=='request': object.__setattr__(confirmed.request.financial,'position_quantity',100.)
    else: context.profile.owner.complete_requests((confirmed.request,),boundary_ms=25000)
    with pytest.raises(ValueError): require_prepared_structural_rejection_exit(evidence)


@pytest.mark.parametrize('kind',('reason','quantity','policy','parent','batch'))
def test_mapper_rejects_foreign_intent_and_noncanonical_parent_ids(monkeypatch,kind):
    _,confirmed,intent,_=prepared(monkeypatch)
    parent=PARENT;batch=BATCH
    if kind=='reason': intent=replace(intent,reason='liquidity_fade_failure')
    elif kind=='quantity': intent=replace(intent,quantity=100.)
    elif kind=='policy': intent=replace(intent,execution_policy=None)
    elif kind=='parent': parent='00000000-0000-0000-0000-000000000000'
    else: batch='00000000000000000000000000000012'
    with pytest.raises(ValueError):
        prepare_structural_rejection_exit(confirmed,intent,parent_record_id=parent,batch_id=batch)
