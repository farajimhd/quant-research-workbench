"""Canonical producer coverage versus ordinal transport order, preserving exact gates."""
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import ast,subprocess
import pytest
from test_fixed_structural_lots import fixture
from src.trading_runtime import fixed_structural_lot_interval_validator_v2 as validator
from src.trading_runtime.fixed_structural_lot_entry import _validate_interval_ticker
from src.trading_runtime.strategy_one_v7_intervals import interval_hash
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan


def ordinal_transport_plan(plan):
    # Opaque producer level IDs need not sort in geometry ordinal order.
    rows=tuple(replace(row,level_id='Z'+row.level_id) if row.ordinal==1 else row
        for row in plan.intervals[0][1])
    rows=tuple(sorted(rows,key=lambda row:(row.valid_from_ms,row.ordinal,row.level_id)))
    canonical=tuple(sorted(rows,key=lambda row:(row.valid_from_ms,row.level_id)))
    assert interval_hash(rows)!=interval_hash(canonical)
    unit=replace(plan.coverage[0],interval_hash=interval_hash(canonical))
    return replace(plan,coverage=(unit,),intervals=((unit.ticker,rows),))


def test_producer_canonical_hash_accepts_ordinal_transport_without_reordering_geometry():
    day,proposal,plan=fixture()
    plan=ordinal_transport_plan(plan);rows=plan.intervals[0][1]
    with pytest.raises(ValueError,match='source content differs'):
        _validate_interval_ticker(plan,session_date=day,ticker='AAA')
    assert validator.validate_fixed_structural_lot_interval_ticker(plan,
        session_date=day,ticker='AAA') is plan.coverage[0]
    assert validator.validate_fixed_structural_lot_interval_plan(plan,
        session_date=day)==plan.coverage
    assert plan.intervals[0][1] is rows


@pytest.mark.parametrize('change',['price','clock-hash','clock-count','interval-count','raw-hash',
    'wrong-type','duplicate-level','causal-clock','source-attempt','source-reference'])
def test_canonical_order_never_accepts_tampered_or_invalid_children(change):
    day,proposal,plan=fixture();plan=ordinal_transport_plan(plan)
    unit=plan.coverage[0];rows=plan.intervals[0][1]
    if change=='price':rows=(replace(rows[0],upper=rows[0].upper+.01),*rows[1:])
    elif change=='clock-hash':unit=replace(unit,clock_hash='f'*64)
    elif change=='clock-count':unit=replace(unit,clock_count=unit.clock_count+1)
    elif change=='interval-count':unit=replace(unit,interval_count=unit.interval_count+1)
    elif change=='raw-hash':unit=replace(unit,interval_hash=interval_hash(rows))
    elif change=='wrong-type':rows=(replace(rows[0],historical=1),*rows[1:])
    elif change=='duplicate-level':rows=(replace(rows[0],level_id=rows[1].level_id),*rows[1:])
    elif change=='causal-clock':rows=(replace(rows[0],confirmed_at_ms=10**15),*rows[1:])
    elif change=='source-attempt':unit=replace(unit,attempt_id='not-a-uuid')
    else:unit=replace(unit,source_checkpoint_hash='not-a-digest')
    changed=replace(plan,coverage=(unit,),intervals=(('AAA',rows),))
    with pytest.raises((ValueError,RuntimeError)):
        validator.validate_fixed_structural_lot_interval_plan(changed,session_date=day)


def test_complete_factory_validates_plan_once_and_each_ticker_child_once(monkeypatch):
    day,proposal,plan=fixture();plan=ordinal_transport_plan(plan)
    second=replace(plan.coverage[0],ticker='ZZZ',attempt_id=str(uuid4()))
    plan=replace(plan,coverage=(*plan.coverage,second),
        valid_seconds=(*plan.valid_seconds,('ZZZ',plan.valid_seconds[0][1])),
        intervals=(*plan.intervals,('ZZZ',plan.intervals[0][1])))
    counts=dict(plan=0,children=[])
    original=CertifiedV7IntervalPlan.__post_init__;children=validator._validate_children
    def full(value):counts['plan']+=1;return original(value)
    def child(*a,**kw):counts['children'].append(a[0]);return children(*a,**kw)
    monkeypatch.setattr(CertifiedV7IntervalPlan,'__post_init__',full)
    monkeypatch.setattr(validator,'_validate_children',child)
    assert validator.validate_fixed_structural_lot_interval_plan(plan,session_date=day)==plan.coverage
    assert counts['plan']==1 and len(counts['children'])==2


def test_versioned_validator_retains_every_original_child_check_ast():
    root=Path(__file__).resolve().parents[1]
    original=ast.parse((root/'src/trading_runtime/fixed_structural_lot_entry.py').read_text(encoding='utf-8'))
    old=next(n for n in original.body if isinstance(n,ast.FunctionDef) and n.name=='_validate_interval_ticker')
    old.name='_validate_interval_ticker_children';del old.body[0]
    expected=ast.unparse(old).replace('unit.interval_hash != interval_hash(rows)',
        'unit.interval_hash != interval_hash(tuple(sorted(rows, key=lambda row: (row.valid_from_ms, row.level_id))))')
    actual=ast.parse((root/'src/trading_runtime/fixed_structural_lot_interval_validator_v2.py').read_text(encoding='utf-8'))
    new=next(n for n in actual.body if isinstance(n,ast.FunctionDef) and n.name=='_validate_interval_ticker_children')
    assert ast.dump(new,include_attributes=False)==ast.dump(ast.parse(expected).body[0],include_attributes=False)


def test_frozen_original_and_published80_modules_unchanged():
    root=Path(__file__).resolve().parents[1]
    for p in ('src/trading_runtime/fixed_structural_lot_entry.py',
            'src/backend/backtest_fixed_structural_lot_source_v2.py',
            'src/backend/backtest_fixed_structural_lot_native_v2.py',
            'src/backend/backtest_fixed_structural_lot_execution_v2.py',
            'src/backend/backtest_fixed_structural_lot_empty_v2.py',
            'src/backend/backtest_fixed_structural_lot_certification_v2.py',
            'src/trading_runtime/fixed_structural_lot_release_v2.py',
            'src/trading_runtime/strategy_eighty_release.py','src/trading_runtime/strategy_eighty_contract.py'):
        baseline=subprocess.check_output(['git','show','9f076e9ef282604e12927c463ea352fcb2ec4174:'+p],cwd=root).decode('utf-8')
        assert (root/p).read_text(encoding='utf-8')==baseline


def test_declared_canonical_session_source_and_original_lifecycle_issue_actual_three_lot_request(monkeypatch):
    from test_fixed_structural_lot_source_v2 import inputs
    from test_fixed_structural_lot_native import cert,declarations
    from src.backend import backtest_fixed_structural_lot_source_v2 as previous
    from src.backend import backtest_fixed_structural_lot_source_v3 as source
    from src.backend import backtest_fixed_structural_lot_native_v3 as native
    from src.backend import backtest_fixed_structural_lot_native as owner
    from src.backend import backtest_fixed_structural_lot_execution_v3 as session
    from src.backend import backtest_strategy_one_execution as execution
    from src.trading_runtime.fixed_structural_lot_release_v3 import derive_fixed_structural_lot_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    plans,authority,old,proposal,calls=inputs(monkeypatch)
    authority=replace(authority,entry_activity_source=replace(authority.entry_activity_source,strategy_number=81))
    plans=replace(plans,v7_intervals=ordinal_transport_plan(plans.v7_intervals))
    for name in ('verify_market_day_plan','certify_candidate_plan','certified_seed_plan','_load_quotes'):
        monkeypatch.setattr(source,name,getattr(previous,name))
    monkeypatch.setattr(source,'certify_v7_interval_plan',lambda *a,**kw:plans.v7_intervals)
    parent,_,_,parent_release=declarations()
    own=cert(derive_fixed_structural_lot_release(parent,parent_release=parent_release,
        release=numbered_strategy(81),policy=old.policy.payload(),approved_code_commit='a'*40,
        approved_code_fingerprint='b'*64,approval_reference='controlled immutable installation seam')['payload'])
    monkeypatch.setattr(source,'certify_numbered_configuration',lambda *a:parent)
    monkeypatch.setattr(native,'load_installed_configuration',lambda *a,**kw:(own,old.policy,'e'*64))
    monkeypatch.setattr(owner,'verify_current_installed_source',lambda own:None)
    monkeypatch.setattr(execution,'prepare_strategy_one_entry_authorities',lambda **kw:
        (plans.candidates,authority.entry_activity_source.gate,None,None,None,(),authority))
    class Client:
        def close(self):calls.append(('closed',))
    actual=session.prepare_fixed_structural_lot_session(plans=plans,number=81,run_id=old.run_id,
        session_date=old.session_date,market=plans.market,candidates=plans.candidates,entry=plans.entry,
        seeds=plans.seeds,through_boundary_ms=57_600_000,client_factory=Client)
    actual.require(market=plans.market,candidates=plans.candidates,entry=plans.entry,
        through_boundary_ms=57_600_000,run_id=old.run_id,number=81)
    from types import SimpleNamespace
    runtime=SimpleNamespace(run_id=old.run_id,config=SimpleNamespace(strategy_id=own.payload['strategy']['strategy_id'],
        strategy_revision=81,anchor_date=old.session_date))
    actual.bind_runtime(runtime)
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
    from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
    original=replace(_proposal(),strategy_number=18,boundary_ms=41000,target_level_id='R3',bos_support_level_id='R3',
        momentum=authority.plan.momentum.lookup('AAA',41000),
        initial_momentum=authority.plan.source.parent.selection_witness('AAA',41000))
    proposal=bind_episode_activity_proposal(authority,
        bind_certified_price_break_proposal(authority.plan,original,strategy_number=36),session_date=old.session_date)
    request=actual.operation.request(proposal);request.verify()
    assert request.revision==81 and [target.price for target in request.entry.targets]==[12.,13.,14.]
    assert runtime._fixed_structural_lot_session is actual
    assert actual.operation.source.intervals is plans.v7_intervals
    assert ('closed',) in calls


@pytest.mark.parametrize('number',[77,80])
def test_unselected_validator_routes_exactly_to_previous_source_facade(monkeypatch,number):
    from src.backend import backtest_fixed_structural_lot_execution_v3 as facade
    from src.backend import backtest_fixed_structural_lot_execution_v2 as previous
    arguments=dict(plans=object(),number=number,run_id=object(),session_date=object(),market=object(),
        candidates=object(),entry=object(),seeds=object(),through_boundary_ms=object(),client_factory=object())
    calls=[];issued=object()
    monkeypatch.setattr(previous,'prepare_fixed_structural_lot_session',lambda **kw:calls.append(kw) or issued)
    assert facade.prepare_fixed_structural_lot_session(**arguments) is issued
    assert calls[0].keys()==arguments.keys()
    assert all(calls[0][key] is value for key,value in arguments.items())


def test_approved_successor_source_and_blank_seal_negative_control(tmp_path):
    import ast
    import importlib.util
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_certification_v3 as actual
    assert len(actual.certify_fixed_structural_lot_source()) == 64
    text = Path(actual.__file__).read_text(encoding='utf-8')
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    for node in reversed(tree.body):
        if isinstance(node, ast.Assign) and node.targets[0].id in {
                'REVIEWED_SOURCE_AST', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST'}:
            name = node.targets[0].id
            value = '{}' if name == 'REVIEWED_SOURCE_AST' else "''"
            lines[node.lineno-1:node.end_lineno] = [name + ' = ' + value + '\n']
    path = tmp_path / 'src/backend/backtest_fixed_structural_lot_certification_v3.py'
    path.parent.mkdir(parents=True)
    path.write_text(''.join(lines), encoding='utf-8')
    spec = importlib.util.spec_from_file_location('unapproved_lot_certifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match='unapproved'):
        module.certify_fixed_structural_lot_source()


def test_whole_source_at2_scope_checks_preserved_with_only_added_canonical_validation():
    root=Path(__file__).resolve().parents[1]
    functions=[]
    for suffix in ('v2','v3'):
        tree=ast.parse((root/f'src/backend/backtest_fixed_structural_lot_source_{suffix}.py').read_text(encoding='utf-8'))
        functions.append(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify_complete_scope'))
    new=functions[1]
    selected=[n for n in new.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
        and isinstance(n.value.func,ast.Name) and n.value.func.id=='validate_fixed_structural_lot_interval_plan']
    assert len(selected)==1
    new.body.remove(selected[0])
    assert ast.dump(new,include_attributes=False)==ast.dump(functions[0],include_attributes=False)


def test_saved_reader_legacy_default_restoration_and_mutated_guard_rejection():
    from hashlib import sha256
    from src.backend.backtest_fixed_v4_certification import _reviewed_fixed_lot_ast_recipe
    root=Path(__file__).resolve().parents[1];path='src/backend/backtest_v4_saved_review.py'
    original=subprocess.check_output(['git','show','9f076e9ef282604e12927c463ea352fcb2ec4174:'+path],cwd=root).decode('utf-8')
    current=(root/path).read_text(encoding='utf-8')
    original_tree=ast.parse(original)
    for name in ('_saved_twenty_price_source','_terminal_attestation','_require_declared_read_profile'):
        node=next(n for n in ast.walk(original_tree) if isinstance(n,ast.FunctionDef) and n.name==name)
        expected=sha256(ast.unparse(node).encode()).hexdigest()
        assert _reviewed_fixed_lot_ast_recipe(current,path.removeprefix('src/'),name,expected)
        changed=ast.parse(current)
        guard=next(n for n in ast.walk(changed) if isinstance(n,ast.FunctionDef) and n.name==name)
        guard.body.insert(0,ast.Return(ast.Constant(None)))
        assert not _reviewed_fixed_lot_ast_recipe(ast.unparse(changed),path.removeprefix('src/'),name,expected)
