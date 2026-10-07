"""Full candidate scope against a wider market and native installed session path."""
from dataclasses import replace
from datetime import date
from uuid import uuid4
import json,re
import pytest
from test_fixed_structural_lot_source import prepared
from test_fixed_structural_lot_native import cert,declarations
from test_backtest_strategy_first_price_source import Bars
from test_backtest_strategy_entry_activity_source import ActivityBars
from src.backend import backtest_fixed_structural_lot_source_v2 as source
from src.backend import backtest_fixed_structural_lot_native_v2 as native
from src.backend import backtest_fixed_structural_lot_native as owner
from src.backend.backtest_strategy_one_candidate_store import _token,project_candidate_plan
from src.backend.backtest_strategy_one_plan import StrategyOneFixedPlans
from src.backend.backtest_market_data import project_market_day_plan
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.trading_runtime.strategy_registry import numbered_strategy


def inputs(monkeypatch, activity_mode='normal'):
    old,proposal,_=prepared(monkeypatch)
    market=old.price_authority.plan.source.market
    candidates=old.price_authority.plan.candidates
    unit=candidates.coverage[0]
    units=tuple(replace(market.units[0],stage=stage,attempt_id=attempt)
        for stage,attempt in zip(('bars','technical','broker_100ms'),unit.source_attempts))
    units+=tuple(replace(u,ticker='ZZZ') for u in units)
    market=replace(market,tickers=('AAA','ZZZ'),units=units)
    coverage=(replace(unit,candidate_count=len(candidates.prepared[0].boundary_ms)),
        replace(unit,ticker='ZZZ',candidate_count=0))
    candidates=replace(candidates,coverage=coverage,token=_token(candidates.source_build_id,
        candidates.candidate_rule_digest,candidates.scan_query_sha256,coverage))
    from src.backend.backtest_strategy_initial_ten_percent import compile_initial_ten_percent_plan
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan,CertifiedPriceReadbackAuthority
    from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
    from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
    from src.backend.backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority
    parent=old.price_authority.plan.source.parent
    from src.backend.backtest_strategy_rising_momentum import _seal
    momentum=parent.momentum
    arrays=tuple(getattr(momentum,n) for n in ('requested_mask','current_boundaries_ms','prior_boundaries_ms',
        'current_line','current_signal','prior_line','prior_signal'))
    momentum=replace(momentum,candidate_plan_token=candidates.token,token=_seal(momentum.source_build_id,
        momentum.market_plan_token,candidates.token,momentum.keys,momentum.source_attempts,arrays))
    initial=compile_initial_ten_percent_plan(candidates,parent.entry,momentum)
    price=compile_certified_price_break_plan(load_first_price_source(market,initial,client=Bars()))
    activity=load_entry_activity_plan(market,price,client=ActivityBars(activity_mode))
    episode=EpisodeActivityReadbackAuthority(old.run_id,compile_episode_activity_static_gate(activity),80)
    authority=CertifiedPriceReadbackAuthority(old.run_id,price,episode)
    seed=CertifiedSeedPlan(market.build_id,'c'*64,({'ticker':'AAA','backtest_session':market.sessions[0]},),'d'*64,True)
    plans=StrategyOneFixedPlans(market,object(),project_market_day_plan(market,('AAA',)),object(),
        candidates,object(),object(),seed,old.intervals,object(),price.entry)
    calls=[]
    monkeypatch.setattr(source,'verify_market_day_plan',lambda m,**kw:calls.append(('full-market',m.tickers)))
    monkeypatch.setattr(source,'certify_candidate_plan',lambda m,**kw:calls.append(('candidates',m.tickers)) or candidates)
    monkeypatch.setattr(source,'certified_seed_plan',lambda m,c:calls.append(('seeds',m.tickers)) or seed)
    def intervals(m,s,**kw):
        calls.append(('intervals',m.tickers,kw['candidate_tickers']))
        assert s is seed
        return old.intervals
    monkeypatch.setattr(source,'certify_v7_interval_plan',intervals)
    monkeypatch.setattr(source,'certify_numbered_configuration',lambda client,number:cert(old.parent_payload))
    monkeypatch.setattr(source,'_load_quotes',lambda m,a,**kw:old.quotes)
    return plans,authority,old,proposal,calls


def test_source_v2_certifies_complete_candidates_not_market_or_admission_survivors(monkeypatch):
    plans,authority,old,proposal,calls=inputs(monkeypatch, activity_mode='fade')
    actual=source.prepare_fixed_structural_lot_source(object(),run_id=old.run_id,parent_number=42,
        session_date=old.session_date,policy=old.policy,plans=plans,price_authority=authority)
    actual.require_prepared_source()
    assert actual.intervals._tickers==('AAA',) and plans.market.tickers==('AAA','ZZZ')
    assert ('intervals',('AAA',),('AAA',)) in calls
    assert ('full-market',('AAA','ZZZ')) in calls
    # The rich native gate retains rejected facts as well as admitted entries.
    assert len(authority.plan.candidates.prepared[0].boundary_ms)>sum(authority.entry_activity_source.gate.rejection_mask == 0)


@pytest.mark.parametrize('change',['execution','execution-token','seed','interval','price','candidate-prefix','missing-entry','missing-seed'])
def test_missing_or_changed_complete_inputs_fail_before_issuance(monkeypatch,change):
    plans,authority,old,proposal,calls=inputs(monkeypatch)
    if change=='execution':plans=replace(plans,execution_market=plans.market)
    elif change=='execution-token':plans=replace(plans,execution_market=replace(plans.execution_market,token='f'*64))
    elif change=='seed':plans=replace(plans,seeds=replace(plans.seeds,token='f'*64))
    elif change=='interval':plans=replace(plans,v7_intervals=replace(plans.v7_intervals,token='f'*64))
    elif change=='price':plans=replace(plans,market=replace(plans.market,token='f'*64))
    elif change=='candidate-prefix':plans=replace(plans,candidates=project_candidate_plan(plans.candidates,through_boundary_ms=31000))
    elif change=='missing-entry':plans=replace(plans,entry=None)
    else:plans=replace(plans,seeds=None)
    with pytest.raises(ValueError):source.prepare_fixed_structural_lot_source(object(),run_id=old.run_id,
        parent_number=42,session_date=old.session_date,policy=old.policy,plans=plans,price_authority=authority)


def test_native_session_bootstrap_reuses_actual_lifecycle_capability(monkeypatch):
    plans,authority,old,proposal,calls=inputs(monkeypatch)
    from src.backend import backtest_fixed_structural_lot_execution_v2 as session
    from src.backend import backtest_strategy_one_execution as execution
    from src.trading_runtime.fixed_structural_lot_release_v2 import derive_fixed_structural_lot_release
    parent,_,_,parent_release=declarations()
    own=cert(derive_fixed_structural_lot_release(parent,parent_release=parent_release,
        release=numbered_strategy(80),policy=old.policy.payload(),approved_code_commit='a'*40,
        approved_code_fingerprint='b'*64,approval_reference='controlled installation seam')['payload'])
    monkeypatch.setattr(source,'certify_numbered_configuration',lambda client,number:parent)
    monkeypatch.setattr(native,'load_installed_configuration',lambda *a,**kw:(own,old.policy,'e'*64))
    monkeypatch.setattr(owner,'verify_current_installed_source',lambda value:None)
    monkeypatch.setattr(execution,'prepare_strategy_one_entry_authorities',lambda **kw:
        (plans.candidates,authority.entry_activity_source.gate,None,None,None,(),authority))
    class Client:
        def close(self):calls.append(('closed',))
    actual=session.prepare_fixed_structural_lot_session(plans=plans,number=80,run_id=old.run_id,
        session_date=old.session_date,market=plans.market,candidates=plans.candidates,entry=plans.entry,
        seeds=plans.seeds,through_boundary_ms=57_600_000,client_factory=Client)
    actual.require(market=plans.market,candidates=plans.candidates,entry=plans.entry,
        through_boundary_ms=57_600_000,run_id=old.run_id,number=80)
    from types import SimpleNamespace
    runtime=SimpleNamespace(run_id=old.run_id,config=SimpleNamespace(strategy_id=own.payload['strategy']['strategy_id'],
        strategy_revision=80,anchor_date=old.session_date))
    actual.bind_runtime(runtime)
    assert runtime._fixed_structural_lot_session is actual
    assert actual.operation.source.intervals._tickers==('AAA',)
    assert ('closed',) in calls
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
    from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
    original=replace(_proposal(),strategy_number=18,boundary_ms=41000,target_level_id='R3',
        bos_support_level_id='R3',momentum=authority.plan.momentum.lookup('AAA',41000),
        initial_momentum=authority.plan.source.parent.selection_witness('AAA',41000))
    proposal=bind_episode_activity_proposal(authority,
        bind_certified_price_break_proposal(authority.plan,original,strategy_number=36),session_date=old.session_date)
    request=actual.operation.request(proposal)
    request.verify()
    assert request.revision==80 and [target.price for target in request.entry.targets]==[12.,13.,14.]


def test_reviewed_successor_seal_and_unapproved_copy_fail_closed(tmp_path):
    import ast
    import re
    from pathlib import Path
    from src.backend.backtest_fixed_structural_lot_certification_v2 import certify_fixed_structural_lot_source
    assert re.fullmatch(r'[0-9a-f]{64}',certify_fixed_structural_lot_source())
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'src/backend/backtest_fixed_structural_lot_certification_v2.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and node.targets[0].id in {
                'REVIEWED_SOURCE_AST','APPROVED_METADATA_ANCHOR','APPROVED_SELF_AST'}:
            node.value=ast.parse('{}' if node.targets[0].id=='REVIEWED_SOURCE_AST' else "''",mode='eval').body
    copied=tmp_path/'src/backend/backtest_fixed_structural_lot_certification_v2.py'
    copied.parent.mkdir(parents=True)
    copied.write_text(ast.unparse(tree),encoding='utf-8')
    namespace={'__file__':str(copied)}
    exec(compile(copied.read_text(encoding='utf-8'),str(copied),'exec'),namespace)
    with pytest.raises(ValueError,match='unapproved'):
        namespace['certify_fixed_structural_lot_source']()


def test_frozen_source_and_immutable77_seal_unchanged():
    from pathlib import Path
    import subprocess
    root=Path(__file__).resolve().parents[1]
    for p in ('src/backend/backtest_fixed_structural_lot_source.py','src/backend/backtest_fixed_structural_lot_certification.py',
              'src/trading_runtime/strategy_seventy_seven_release.py'):
        baseline=subprocess.check_output(['git','show','ca9c7cce781afa53ec81e8c5b219279b7d7fe812:'+p],cwd=root).decode('utf-8')
        assert (root/p).read_text(encoding='utf-8')==baseline


def test_real_complete_candidate_decoder_empty_and_missing_coverage(monkeypatch):
    from test_fixed_structural_lot_empty import EmptyReader
    from test_backtest_strategy_one_candidate_store import Reader,_market,THROUGH
    from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan,RULE_DIGEST
    from src.backend.backtest_market_data import project_empty_market_day_plan
    from src.backend import backtest_fixed_structural_lot_empty_v2 as empty
    from src.backend import backtest_market_data as market_module
    from src.backend import backtest_input_scope as scope
    from src.backend import backtest_fixed_structural_lot_execution_v2 as session
    from src.trading_runtime.fixed_structural_lot_release_v2 import derive_fixed_structural_lot_release
    market=_market(); reader=EmptyReader(); reader.close=lambda:None
    candidates=certify_candidate_plan(market,candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=THROUGH,client=reader)
    plans=StrategyOneFixedPlans(market,object(),project_empty_market_day_plan(market,
        empty_candidate_token=candidates.token),object(),candidates,None,None,None,None,None,None)
    parent,_,_,parent_release=declarations()
    own=cert(derive_fixed_structural_lot_release(parent,parent_release=parent_release,
        release=numbered_strategy(80),policy=source.FixedStructuralLotPolicy().payload(),
        approved_code_commit='a'*40,approved_code_fingerprint='b'*64,
        approval_reference='controlled installation seam')['payload'])
    monkeypatch.setattr(market_module,'verify_market_day_plan',lambda *a,**kw:None)
    monkeypatch.setattr(scope,'input_exclusions',lambda day:())
    monkeypatch.setattr(native,'certify_numbered_configuration',lambda *a:parent)
    monkeypatch.setattr(native,'load_installed_configuration',lambda *a,**kw:(own,source.FixedStructuralLotPolicy(),'e'*64))
    monkeypatch.setattr(owner,'verify_current_installed_source',lambda value:None)
    run=str(uuid4());day=date.fromisoformat(market.sessions[0])
    result=session.prepare_fixed_structural_lot_session(plans=plans,number=80,run_id=run,session_date=day,
        market=market,candidates=candidates,entry=None,seeds=None,through_boundary_ms=THROUGH,client_factory=lambda:reader)
    result.require(market=market,candidates=candidates,entry=None,through_boundary_ms=THROUGH,run_id=run,number=80)
    assert result.operation.source.intervals is None
    with pytest.raises(ValueError,match='no entry'):result.operation.source.request(object())
    with pytest.raises((ValueError,RuntimeError)):
        empty.prepare_empty_fixed_structural_lot_source(Reader(missing=True),plans=plans,number=80,
            run_id=run,session_date=day,through_boundary_ms=THROUGH)


def test_compatibility_bridge_requires_entire_current_and_original_baseline_ast():
    import ast,subprocess
    from pathlib import Path
    from hashlib import sha256
    from src.backend.backtest_fixed_structural_lot_compatibility_v2 import restore_reviewed_parent_source
    root=Path(__file__).resolve().parents[1]
    for p in ('src/backend/replay_run_service.py','src/trading_runtime/strategy_registry.py',
            'src/backend/backtest_strategy_one_configuration.py','src/trading_runtime/numbered_fixed_strategy.py'):
        current=(root/p).read_text(encoding='utf-8')
        baseline=subprocess.check_output(['git','show','ca9c7cce781afa53ec81e8c5b219279b7d7fe812:'+p],cwd=root).decode('utf-8')
        restored=restore_reviewed_parent_source(current,p)
        assert sha256(ast.unparse(ast.parse(restored)).encode()).digest()==sha256(ast.unparse(ast.parse(baseline)).encode()).digest()
        changed=current+'\nUNREVIEWED_BEHAVIOR = True\n'
        assert restore_reviewed_parent_source(changed,p)==changed


def test_complete_candidate_ticker_cannot_be_dropped_from_execution_scope(monkeypatch):
    plans,authority,old,proposal,calls=inputs(monkeypatch)
    coverage=(plans.candidates.coverage[0],replace(plans.candidates.coverage[1],candidate_count=2))
    rows=(*plans.candidates.prepared,replace(plans.candidates.prepared[0],ticker='ZZZ'))
    candidates=replace(plans.candidates,coverage=coverage,prepared=rows,
        token=_token(plans.candidates.source_build_id,plans.candidates.candidate_rule_digest,
            plans.candidates.scan_query_sha256,coverage))
    plans=replace(plans,candidates=candidates)
    monkeypatch.setattr(source,'certify_candidate_plan',lambda *a,**kw:candidates)
    with pytest.raises(ValueError,match='exact complete candidate execution projection'):
        source.verify_complete_scope(plans,client=object(),session_date=old.session_date,price_authority=authority)


def test_empty_prefix_cannot_skip_nonempty_candidate_seed_certification(monkeypatch):
    plans,authority,old,proposal,calls=inputs(monkeypatch)
    plans=replace(plans,seeds=replace(plans.seeds,token='f'*64))
    with pytest.raises(ValueError,match='seed linkage'):
        source.verify_complete_scope(plans,client=object(),session_date=old.session_date,
            through_boundary_ms=100,require_price_authority=False)


def test_source_v1_session_delegates_exact_unchanged_arguments(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_execution_v2 as facade
    from src.backend import backtest_fixed_structural_lot_execution as legacy
    arguments=dict(number=77,run_id=object(),session_date=object(),market=object(),
        candidates=object(),entry=object(),seeds=object(),through_boundary_ms=object(),client_factory=object())
    issued=object();calls=[]
    monkeypatch.setattr(legacy,'prepare_fixed_structural_lot_session',lambda **kw:calls.append(kw) or issued)
    assert facade.prepare_fixed_structural_lot_session(plans=object(),**arguments) is issued
    assert len(calls)==1 and calls[0].keys()==arguments.keys()
    assert all(calls[0][key] is value for key,value in arguments.items())
