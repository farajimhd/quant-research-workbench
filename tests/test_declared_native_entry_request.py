"""Versioned entry-request declaration; no sizing or financial acceptance."""
from dataclasses import FrozenInstanceError

import pytest

from src.trading_runtime.declared_native_entry_request import (
    DeclaredEntryRequestPolicy, inherited_fixed_entry_request_policy, parse_declared_entry_request,
)


def test_exact_complete_request_is_detached_and_immutable():
    policy=inherited_fixed_entry_request_policy()
    value=policy.payload()
    assert parse_declared_entry_request(value)==policy
    assert value['capital_request']['fraction']==[1,3]
    assert value['capital_request']['allow_replacement'] is False
    assert value['execution_policy']['envelope']['maximum_buy_price_rule']=='proposal_reference_ask'
    assert value['protection_profile']['slices'][0]['quantity_fraction']==[1,1]
    assert value['outside_rth_rule']=='declared_extended_session_windows'
    value['capital_request']['fraction'][1]=2
    assert policy.payload()['capital_request']['fraction']==[1,3]
    with pytest.raises(FrozenInstanceError):policy.declaration_json='foreign'


@pytest.mark.parametrize('change',['missing','unknown','fraction','fraction_alias','replacement',
    'quantity','cap','reprice','partial_fill','quote','slice','stop','trailing','repair','session','reason'])
def test_request_drift_cannot_change_economics_routing_protection_or_session(change):
    value=inherited_fixed_entry_request_policy().payload()
    if change=='missing':del value['execution_policy']
    elif change=='unknown':value['private_override']=True
    elif change=='fraction':value['capital_request']['fraction']=[1,2]
    elif change=='fraction_alias':value['capital_request']['fraction']=[1.0,3]
    elif change=='replacement':value['capital_request']['allow_replacement']=True
    elif change=='quantity':value['requested_quantity']=1.0
    elif change=='cap':value['execution_policy']['envelope']['maximum_buy_price_rule']='no_cap'
    elif change=='reprice':value['execution_policy']['envelope']['maximum_reprices']=0
    elif change=='partial_fill':value['execution_policy']['partial_fill_policy']='accept_partial'
    elif change=='quote':value['execution_policy']['quote_source']='ibkr'
    elif change=='slice':value['protection_profile']['slices'][0]['quantity_fraction']=[1,2]
    elif change=='stop':value['protection_profile']['slices'][0]['stop']['price_rule']='future_low'
    elif change=='trailing':value['protection_profile']['slices'][0]['trailing']['rule_type']='broker_percent'
    elif change=='repair':value['protection_profile']['emergency_repair_deadline_ms']=999
    elif change=='session':value['outside_rth_rule']='always_true'
    else:value['intent_reason']='legacy_rebadge'
    with pytest.raises(ValueError):parse_declared_entry_request(value)


@pytest.mark.parametrize('value',[None,True,{},'{}'])
def test_untyped_or_incomplete_request_cannot_enable_entry(value):
    with pytest.raises(ValueError):DeclaredEntryRequestPolicy(value)
