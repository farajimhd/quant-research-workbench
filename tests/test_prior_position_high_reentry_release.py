"""Preparation-only whole-tree reconstruction; no installed source admission."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
from test_strategy_sixty_six_release import source_fixture
from src.trading_runtime import strategy_forty_two_release
from src.trading_runtime.strategy_registry import NumberedStrategyRelease
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
from src.trading_runtime.prior_position_high_reentry import *
from src.trading_runtime.prior_position_high_reentry_release import *


def case():
    source=source_fixture()
    # This synthetic prepared graph is not an actual normalized DB certificate.
    source=replace(source,payload_hash=sha256(canonical_json(source.payload).encode()).hexdigest())
    parent=strategy_forty_two_release.release_contract()
    m=source.payload['strategy']['numbered_release']
    binding=ReentryParentBinding(parent,source.revision()['revision_id'],source.payload_hash,
        source.node_hash,source.source_candidate_id,source.source_candidate_hash,
        m['approved_code_commit'],m['approved_code_fingerprint'])
    declaration=replace(parent,number=9001,executor_revision=9001,
        input_contracts=parent.input_contracts+('declared-numbered-fixed-policy-adapter@1',PRIOR_POSITION_HIGH_REENTRY_INPUT),
        rule_set_contracts=parent.rule_set_contracts+(PRIOR_POSITION_HIGH_REENTRY_RULE,),
        behavior_specification='Unregistered component reentry declaration',approved_digest='')
    declaration=replace(declaration,approved_digest=declaration.digest())
    approval=dict(approved_code_commit='a'*40,approved_code_fingerprint='b'*64,approval_reference='component-only')
    return source,binding,declaration,approval


def test_whole_parent_economics_exits_and_policy_tree_preserved():
    source,binding,release,approval=case();before=deepcopy(source.payload)
    result=derive_reentry_configuration(source,binding=binding,release=release,**approval)
    assert verify_prepared_reentry_configuration(result,source,binding=binding,release=release)==result
    assert source.payload==before
    for key in before.keys()-{'strategy','strategy_profile','run_plan'}:
        assert result['payload'][key]==before[key]
    identity={'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}
    for key in before['strategy'].keys()-identity:
        assert result['payload']['strategy'][key]==before['strategy'][key]
    old=before['strategy']['numbered_release'];new=result['payload']['strategy']['numbered_release']
    metadata={'contract','approved_digest','approved_code_commit','approved_code_fingerprint','approval_reference',
        'publication_mode','source_revision_id','source_payload_hash','manifest_hash'}
    assert {k:new[k] for k in old.keys()-metadata}=={k:old[k] for k in old.keys()-metadata}
    assert set(new)-set(old)=={'prior_position_high_reentry_policy'}


@pytest.mark.parametrize('mutation',['cash','policy','ticker_key','date_key','assignment','nodes','bool_policy'])
def test_resealed_full_payload_mutations_rejected(mutation):
    source,binding,release,approval=case()
    result=derive_reentry_configuration(source,binding=binding,release=release,**approval)
    if mutation=='cash': result['payload']['unexpected_cash']=10001
    elif mutation=='policy': result['payload']['strategy']['numbered_release']['add_policy']['allows_adds']=True
    elif mutation=='bool_policy': result['payload']['strategy']['numbered_release']['prior_position_high_reentry_policy']['every_reentry']=1
    elif mutation=='assignment': result['payload']['assignments']=[{'foreign':True}]
    elif mutation=='nodes': result['node_hash']='c'*64
    else: result['payload'][mutation]=['EXCEPTION']
    m=result['payload']['strategy']['numbered_release']
    m['manifest_hash']=sha256(canonical_json({k:v for k,v in m.items() if k!='manifest_hash'}).encode()).hexdigest()
    result['payload_hash']=sha256(canonical_json(result['payload']).encode()).hexdigest()
    if mutation!='nodes': result['node_hash']=node_hash(encode_nodes(result['payload']))
    with pytest.raises(ValueError): verify_prepared_reentry_configuration(result,source,binding=binding,release=release)


@pytest.mark.parametrize('field',['payload_hash','node_hash','source_candidate_hash','source_candidate_id'])
def test_foreign_parent_identity_rejected(field):
    source,binding,release,approval=case()
    with pytest.raises(ValueError): derive_reentry_configuration(replace(source,**{field:'c'*64}),binding=binding,release=release,**approval)


def test_changed_declared_prefix_cannot_add_exit_extension():
    source,binding,release,approval=case()
    release=replace(release,rule_set_contracts=release.rule_set_contracts+('foreign-risk-exit@1',),approved_digest='')
    release=replace(release,approved_digest=release.digest())
    with pytest.raises(ValueError): derive_reentry_configuration(source,binding=binding,release=release,**approval)
