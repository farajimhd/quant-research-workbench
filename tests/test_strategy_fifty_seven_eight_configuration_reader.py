"""Actual child reader validates typed nodes and derives exact published50 parent.

Only parent acquisition is replaced by the pinned certified50 test fixture;
child row/node decoding, manifest verification, hashing and inheritance are real.
"""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import pytest
from test_strategy_fifty_two_release import source_fixture
from test_strategy_thirty_three_configuration import APPROVAL
from test_strategy_two_configuration import Reader
from src.backend import backtest_strategy_one_configuration as module
from src.trading_runtime import strategy_fifty_seven_release as quarter
from src.trading_runtime import strategy_fifty_eight_release as half
from src.trading_runtime.journal_contract import canonical_json


def prepared(monkeypatch,number,policy):
    parent=source_fixture()
    word='seven' if number==57 else 'eight'
    envelope=getattr(policy,f'derive_strategy_fifty_{word}_configuration')(parent,**APPROVAL)
    client=Reader()
    client.payloads[number]=deepcopy(envelope['payload'])
    client.sources[number]=(envelope['source_candidate_id'],envelope['source_candidate_hash'])
    actual=module.certify_numbered_configuration
    parents=[]
    def certified(reader,n=1):
        if n==50:
            assert reader is client
            parents.append(n)
            return parent
        return actual(reader,n)
    monkeypatch.setattr(module,'certify_numbered_configuration',certified)
    return client,envelope,parent,parents,actual


@pytest.mark.parametrize('number,policy',[(57,quarter),(58,half)])
def test_actual_reader_certifies_correct_typed_child_and_selector(monkeypatch,number,policy):
    client,envelope,parent,parents,actual=prepared(monkeypatch,number,policy)
    result=actual(client,number)
    assert result.payload==envelope['payload']
    assert result.strategy_number==number and result.payload_hash==envelope['payload_hash']
    assert result.node_hash==envelope['node_hash'] and len(result.token)==64
    assert parents==[50]
    assert len(client.queries)==2
    assert all(f'strategy_number={number}'in query for query in client.queries)
    assert 'configuration_release'in client.queries[0] and 'ORDER BY node_id'in client.queries[1]
    revision=module.selected_numbered_revision(client=client,revision_id=result.revision()['revision_id'])
    assert revision['revision']==number and revision['content_hash']==result.payload_hash
    assert parent.payload['strategy']['strategy_number']==50


@pytest.mark.parametrize('number,policy',[(57,quarter),(58,half)])
@pytest.mark.parametrize('mutation',['inheritance','source_id','source_hash','parent_hash','node_hash','foreign_number'])
def test_actual_reader_rejects_resealed_overrides_or_foreign_source(monkeypatch,number,policy,mutation):
    client,envelope,parent,parents,actual=prepared(monkeypatch,number,policy)
    if mutation=='inheritance':
        client.payloads[number]['strategy']['parameters']['execution']['tick_size']=.02
    elif mutation=='source_id':client.sources[number]=('foreign-parent',envelope['source_candidate_hash'])
    elif mutation=='source_hash':client.sources[number]=(envelope['source_candidate_id'],'e'*64)
    elif mutation=='parent_hash':
        monkeypatch.setattr(module,'certify_numbered_configuration',lambda reader,n:replace(parent,payload_hash='e'*64)if n==50 else actual(reader,n))
    elif mutation=='foreign_number':
        sibling=half if number==57 else quarter
        word='eight'if number==57 else 'seven'
        client.payloads[number]=getattr(sibling,f'derive_strategy_fifty_{word}_configuration')(parent,**APPROVAL)['payload']
    elif mutation=='node_hash':
        execute=client.execute
        def corrupt(query):
            output=execute(query)
            if 'configuration_release'in query:
                value=json.loads(output);value['node_hash']='f'*64;return json.dumps(value)
            return output
        client.execute=corrupt
    # Reader regenerates all typed hashes for payload alterations, so hidden
    # inherited changes must fail exact derivation, not merely stale hashes.
    with pytest.raises((ValueError,RuntimeError)):
        actual(client,number)
