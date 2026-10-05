"""Negative-regime ablation preserves immutable policies and native authority."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.trading_runtime import strategy_forty_eight_release as release
from src.trading_runtime.early_original_risk_failure import EarlyOriginalRiskPolicy, early_original_risk_failure
from src.trading_runtime.strategy_followthrough_exit import validate_witness
from src.trading_runtime.arte_followthrough_failure_v4 import restore_failure, seal_followthrough_rows
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from test_early_original_risk_failure import observation, POLICY
from test_strategy_forty_six_release import source_fixture, APPROVAL
from test_strategy_forty_six_failure_route import fixture as failure_graph, witness
from test_arte_episode_activity_v4 import graph37, seal
from test_strategy_forty_two_management import prepared_manager


@pytest.mark.parametrize('line,signal,qualifies', [(.01,.02,False),(-.01,.01,False),(-.01,0.,False),(-.02,-.01,True)])
def test48_extension_requires_both_completed_macd_values_strictly_negative(line, signal, qualifies):
    value = observation(macd_line=line, macd_signal=signal)
    assert (early_original_risk_failure(value, policy=release.EARLY_FAILURE_POLICY) is not None) == qualifies
    assert early_original_risk_failure(value, policy=POLICY) is not None


@pytest.mark.parametrize('flag', [0, 1, None, 'true'])
def test_negative_regime_flag_is_strict_boolean(flag):
    with pytest.raises(ValueError):
        EarlyOriginalRiskPolicy('invalid', None, (1,4), require_negative_regime=flag)


def test_old46_47_payload_and_release_sources_are_immutable():
    from src.trading_runtime import strategy_forty_six_release as old46, strategy_forty_seven_release as old47
    assert 'require_negative_regime' not in old46.EARLY_FAILURE_POLICY.payload()
    assert old46.EARLY_FAILURE_POLICY.payload() == old47.EARLY_FAILURE_POLICY.payload()
    assert release.EARLY_FAILURE_POLICY.payload()['require_negative_regime'] is True
    for name in ('strategy_forty_six_release.py', 'strategy_forty_seven_release.py'):
        relative = 'src/trading_runtime/' + name
        assert Path(relative).read_text(encoding='utf-8') == subprocess.check_output(
            ['git','show','9bf01966f:'+relative],text=True,encoding='utf-8')


def test48_derivation_preserves_parent42_nonidentity_payload_and_inherited_policies():
    from src.trading_runtime import strategy_forty_two_release as parent
    source = source_fixture()
    before = deepcopy(source.payload)
    result = release.derive_strategy_forty_eight_configuration(source, **APPROVAL)
    assert source.payload == before
    assert release.PARENT_REVISION_ID == 'strategy-one-42:61d09336-6eb1-4298-bc8e-1b985e97aa78'
    assert release.PARENT_PAYLOAD_HASH == '048fbd8a27269fcb7c12e1c49d7213e2eb08320a42d86e355d8e55fbdbce37b6'
    assert release.release_contract().input_contracts == parent.release_contract().input_contracts
    assert release.release_contract().rule_set_contracts == parent.release_contract().rule_set_contracts + (release.EARLY_FAILURE_POLICY.policy_id,)
    for key in before.keys() - {'strategy','strategy_profile','run_plan'}:
        assert before[key] == result['payload'][key]
    for key in before['strategy'].keys() - {'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}:
        assert before['strategy'][key] == result['payload']['strategy'][key]
    for key in release.INHERITED_POLICIES:
        assert before['strategy']['numbered_release'][key] == result['payload']['strategy']['numbered_release'][key]
    assert release.verify_prepared_strategy_forty_eight_manifest(result['payload']['strategy'])
    for field, wrong in [('payload_hash','f'*64),('attempt_id','00000000-0000-0000-0000-000000000001')]:
        with pytest.raises(ValueError, match='exact pinned'):
            release.derive_strategy_forty_eight_configuration(replace(source, **{field:wrong}), **APPROVAL)


def test48_exact_native_entry_activity_sealer_and_wrong_number_rejection():
    args = graph37(48)
    sealed = seal(*args)
    assert sealed[0]['strategy_number'] == 48
    assert seal(sealed[0], *args[1:]) == sealed
    with pytest.raises(ValueError, match='another numbered entry'):
        seal(*args[:4], replace(args[4],strategy_number=47))


def test48_failure_projection_restore_and_parent47_witness_economics():
    w = witness()
    # Quarter risk and a negative regime qualify48, preserving exact source facts.
    intent, base, row, source, event, entry = failure_graph(w,48)
    families = dict(_sealed_families(base))
    sealed = seal_followthrough_rows(None,(row,),
        (source,*families['trading_strategy_intent_v1']),(event,*base.events),(entry,))
    assert restore_failure(sealed[0]) == w
    positive = replace(w,macd_line=.01,macd_signal=.02)
    validate_witness(positive,strategy_number=47)
    with pytest.raises(ValueError,match='pinned rule'):
        validate_witness(positive,strategy_number=48)
    assert intent.metadata == {} and intent.reference_price == w.bid


@pytest.mark.parametrize('number,negative,expected', [(42,False,0),(47,False,1),(48,False,0),(48,True,1)])
def test48_management_rejects_positive_regime_regression_and_preserves_old_rules(number,negative,expected):
    manager, w, financial, rows = prepared_manager(number)
    manager.contract = numbered_fixed_strategy(number)
    key = (financial.account_id,financial.assignment_id,financial.ticker)
    source = manager._submitted[key]
    at = w.completed_five_second_boundary_ms
    manager._first_held_boundaries[key] = at-30_000
    price = int((3*source.reference_ask+source.initial_stop)/4*10_000)-1
    frame = rows(at,completed=True,age=48)
    frame[5000].update(close_int=price,macd_line=-.02 if negative else .01,macd_signal=-.01 if negative else .02)
    evidence = asyncio.run(manager.evidence.management_evidence(financial.ticker,frame,boundary_ms=at))
    manager.evidence.management_evidence = AsyncMock(return_value=replace(evidence,bid=price/10_000,ask=price/10_000+.01))
    # Keep this an extension-only ablation rather than inherited dual-timeframe AH failure.
    assert 10000 not in frame
    asyncio.run(manager.on_management(financial,frame,at))
    assert manager.runtime.submit_followthrough_failure.await_count == expected
    manager.runtime.submit_confirmed_ah_failure.assert_not_awaited()
    if expected:
        validate_witness(manager.runtime.submit_followthrough_failure.await_args.args[1],strategy_number=number)


def test48_compiler_envelope_and_full_projection_certificate_with_mutation(tmp_path):
    from pipelines.strategy_one.strategy_forty_eight_configuration import compile_strategy_forty_eight_configuration
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    from src.backend.backtest_strategy_forty_eight_certification import certify_strategy_forty_eight_source
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    result = compile_strategy_forty_eight_configuration(source_fixture(),**APPROVAL)
    payload,nodes = _verified_numbered_envelope(result)
    assert payload['strategy']['strategy_number'] == 48 and len(nodes) == result['node_count']
    assert numbered_strategy_parent(48) == 42
    assert len(certify_numbered_fixed_v4_projection(48)) == 64
    relative = 'src/trading_runtime/strategy_forty_eight_release.py'
    changed = tmp_path/'changed48.py'
    changed.write_text(Path(relative).read_text(encoding='utf-8')+'\nUNREVIEWED=True\n',encoding='utf-8')
    with pytest.raises(ValueError,match='pinned release source changed'):
        certify_strategy_forty_eight_source(source_overrides={relative:changed})


def test48_cold_oms_requires_exact_numbered_witness_and_restores_admitted_intent():
    from src.trading_runtime.arte_oms_projection import _approved_strategy_one_oms_intent
    original, base, row, *_ = failure_graph(witness(),48)
    state = SimpleNamespace(sequence=2,group=dict(account_id='DU1',strategy_revision=48,group_id='group-48'))
    source = SimpleNamespace(intent=original,record_id=row['parent_record_id'],batch_id=row['batch_id'])
    history = SimpleNamespace(run_id=base.run_id,records=(),through_sequence=2)
    reservation = dict(account_id='DU1',intent_id=original.intent_id,reservation_id='reservation',
        decision_id='decision',account_key='cash',assignment_id='assignment-1',quantity=10.)
    decision = dict(reservation_id='reservation',decision_id='decision',account_key='cash',
        status='approved',policy_id='policy',policy_revision=1,requested_quantity=10.)
    approved, records = _approved_strategy_one_oms_intent(state,source,history,reservation,decision,followthrough_row=row)
    assert approved.intent_id == original.intent_id and approved.quantity == 10
    assert approved.metadata['assignment_id'] == 'assignment-1' and records == ()
    state.group['strategy_revision'] = 47
    with pytest.raises(ValueError,match='committed scalar witness'):
        _approved_strategy_one_oms_intent(state,source,history,reservation,decision,followthrough_row=row)


@pytest.mark.parametrize('changed_release',[False,True])
def test48_terminal_saved_review_requires_exact_release_and_native_source(monkeypatch,changed_release):
    from test_backtest_v4_saved_review import test_terminal_review_requires_release_and_native_source
    test_terminal_review_requires_release_and_native_source(monkeypatch,changed_release,48)


def test48_publisher_plan_defaults_to_exact42_parent_without_publication(monkeypatch,capsys):
    import sys
    from scripts.clickhouse import publish_strategy_forty_eight_configuration as command
    from scripts.clickhouse import smoke_strategy_one_backtest as smoke
    from src.backend import backtest_v3_clients as clients
    calls=[]
    monkeypatch.setattr(command,'approved_source',lambda commit:'a'*64)
    monkeypatch.setattr(smoke,'_load_private_credentials',lambda:None)
    reader=SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(clients,'v3_client',lambda principal:reader)
    def certified(actual,number):
        assert actual is reader and number == 42
        calls.append(number)
        return source_fixture()
    monkeypatch.setattr(command,'certify_numbered_configuration',certified)
    monkeypatch.setattr(command,'compile_strategy_forty_eight_configuration',
        lambda source,**kwargs:release.derive_strategy_forty_eight_configuration(source,**kwargs))
    monkeypatch.setattr(sys,'argv',['publish48','--approved-code-commit','a'*40,'--approval-reference','test-plan'])
    assert command.main() == 0 and calls == [42]
    assert 'no publication performed' in capsys.readouterr().out
