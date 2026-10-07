"""Actual immutable base source controls, never certificate/source overrides."""
import ast
from dataclasses import asdict
import subprocess
import sys
import types

import pytest

from src.trading_runtime.strategy_one_position import advance_protection,confirm_protection_transition
from tests.test_strategy_one_position import opening,level

BASE='a7b47a38632cf83bab8e0fc0c165b71d7b30e35a'


def source(relative):
    return subprocess.check_output(['git','show',BASE+':'+relative]).decode('utf8')


@pytest.fixture(scope='module')
def legacy():
    name='src.trading_runtime._fixed_lot_immutable_base_control'
    module=types.ModuleType(name)
    module.__package__='src.trading_runtime'
    sys.modules[name]=module
    exec(compile(source('src/trading_runtime/strategy_one_position.py'),'<immutable-a7-protection-control>','exec'),module.__dict__)
    yield module
    del sys.modules[name]


@pytest.mark.parametrize('trailing',[False,True])
@pytest.mark.parametrize('escalation',[False,True])
@pytest.mark.parametrize('quote_only',[False,True])
@pytest.mark.parametrize('low',[None,99500])
def test_default_none_complete_transition_and_refused_confirm_matches_base(legacy,trailing,escalation,quote_only,low):
    now=opening().state
    old=legacy.ProtectionState(**asdict(now))
    inputs=dict(now_ms=31000,bid=10.,ask=10.01,tick=.01,
        low_boundary_ms=30000 if low is not None else None,low_int=low,
        low_price_valid=low is not None,low_extremes_valid=low is not None,
        overhead_levels=[level(i,11+i*.1) for i in range(1,9)],price_bearing_bar=not quote_only,
        allows_completed_30s_trailing=trailing,allows_target_escalation=escalation)
    geometry=[(1,9.8),(2,9.9),(3,10.1)]
    expected=legacy.advance_protection(old,breaks=[legacy.ResistanceBreak(31000,level(i,c)) for i,c in geometry],**inputs)
    from src.trading_runtime.strategy_one_position import ResistanceBreak
    actual=advance_protection(now,breaks=[ResistanceBreak(31000,level(i,c)) for i,c in geometry],stop_ceiling=None,**inputs)
    assert asdict(actual)==asdict(expected)
    for stop_confirmed in (False,actual.stop_amendment is not None):
        for target_confirmed in (False,actual.target_amendment is not None):
            assert asdict(confirm_protection_transition(now,actual,stop_confirmed=stop_confirmed,
                target_confirmed=target_confirmed,stop_ceiling=None))==asdict(legacy.confirm_protection_transition(
                    old,expected,stop_confirmed=stop_confirmed,target_confirmed=target_confirmed))


@pytest.mark.parametrize('malformation',['clock','quote','groupcount','stop','target'])
def test_default_none_errors_match_base(legacy,malformation):
    from dataclasses import replace
    state=opening().state
    changes={'groupcount':{'earned_groups':1},'stop':{'stop':11.},'target':{'target':float('nan')}}
    if malformation in changes: state=replace(state,**changes[malformation])
    old=legacy.ProtectionState(**asdict(state))
    inputs=dict(now_ms=31000,bid=10.,ask=10.01,tick=.01,low_boundary_ms=None,low_int=None,
        low_price_valid=False,low_extremes_valid=False,breaks=(),overhead_levels=(),price_bearing_bar=False)
    if malformation=='clock': inputs['now_ms']=30100
    if malformation=='quote': inputs['ask']=float('nan')
    with pytest.raises(Exception) as expected: legacy.advance_protection(old,**inputs)
    with pytest.raises(type(expected.value)) as actual: advance_protection(state,stop_ceiling=None,**inputs)
    assert str(actual.value)==str(expected.value)


class RestoreDefault(ast.NodeTransformer):
    def visit_FunctionDef(self,node):
        if node.name=='_validate_stop_ceiling': return None
        pairs=[(arg,default) for arg,default in zip(node.args.kwonlyargs,node.args.kw_defaults)
               if arg.arg!='stop_ceiling']
        node.args.kwonlyargs=[arg for arg,_ in pairs]
        node.args.kw_defaults=[default for _,default in pairs]
        node.body=[v for v in node.body if not (isinstance(v,ast.If)
            and ast.unparse(v.test)=='stop_ceiling is not None')]
        return self.generic_visit(node)
    def visit_IfExp(self,node):
        if ast.unparse(node.test)=='stop_ceiling is None': return self.visit(node.body)
        return self.generic_visit(node)


@pytest.mark.parametrize('relative',['src/trading_runtime/strategy_one_position.py',
                                    'src/trading_runtime/strategy_one_protection_snapshot.py'])
def test_exact_whole_module_ast_restores_after_only_reviewed_optional_ceiling(relative):
    from pathlib import Path
    current=RestoreDefault().visit(ast.parse(Path(relative).read_text(encoding='utf8')))
    baseline=ast.parse(source(relative))
    if relative.endswith('strategy_one_protection_snapshot.py'):
        from copy import deepcopy
        original=next(v for v in baseline.body if isinstance(v,ast.FunctionDef) and v.name=='project_protection_snapshot')
        serializer=next(v for v in current.body if isinstance(v,ast.FunctionDef) and v.name=='_serialize_protection_snapshot')
        expected=deepcopy(original);expected.name='_serialize_protection_snapshot'
        for statement in expected.body:
            if isinstance(statement,ast.For):
                statement.body=[v for v in statement.body if not (isinstance(v,ast.Expr)
                    and isinstance(v.value,ast.Call) and isinstance(v.value.func,ast.Name)
                    and v.value.func.id=='_validate_state')]
        assert ast.dump(serializer,include_attributes=False)==ast.dump(expected,include_attributes=False)
        wrapper=next(v for v in current.body if isinstance(v,ast.FunctionDef) and v.name=='project_protection_snapshot')
        assert len(wrapper.body)==4 and isinstance(wrapper.body[1],ast.If)
        original_loop=next(v for v in original.body if isinstance(v,ast.For))
        assert ast.dump(wrapper.body[1],include_attributes=False)==ast.dump(original.body[1],include_attributes=False)
        assert ast.dump(wrapper.body[2],include_attributes=False)==ast.dump(ast.For(target=original_loop.target,
            iter=original_loop.iter,body=original_loop.body[:3],orelse=[]),include_attributes=False)
        assert ast.unparse(wrapper.body[3])==('return _serialize_protection_snapshot(run_id=run_id, '
            'session_date=session_date, checkpoint_sequence=checkpoint_sequence, '
            'boundary_ms=boundary_ms, positions=positions)')
        # Reinlining is permitted only after the actual extracted serializer and
        # validation wrapper independently match the original body fragments.
        current.body=[deepcopy(original) if v is wrapper else v for v in current.body if v is not serializer]
        loader=next(v for v in current.body if isinstance(v,ast.FunctionDef) and v.name=='_load_protection_snapshot_rows')
        public=next(v for v in current.body if isinstance(v,ast.FunctionDef) and v.name=='load_protection_snapshot_rows')
        old_loader=next(v for v in baseline.body if isinstance(v,ast.FunctionDef) and v.name=='load_protection_snapshot_rows')
        restored=deepcopy(loader);restored.name=old_loader.name
        restored.args.kwonlyargs=restored.args.kwonlyargs[:-1];restored.args.kw_defaults=restored.args.kw_defaults[:-1]
        branches=[v for v in restored.body if isinstance(v,ast.If) and ast.unparse(v.test)=='_selected_positions is None']
        assert len(branches)==1
        restored.body=[part for v in restored.body for part in (v.body if v is branches[0] else [v])]
        assert ast.dump(restored,include_attributes=False)==ast.dump(old_loader,include_attributes=False)
        assert len(public.body)==1 and ast.unparse(public.body[0])=='return _load_protection_snapshot_rows(client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)'
        current.body=[deepcopy(old_loader) if v is public else v for v in current.body if v is not loader]
    assert ast.dump(current,include_attributes=False)==ast.dump(baseline,include_attributes=False)


@pytest.mark.parametrize('empty',[False,True])
def test_default_protection_serializer_rows_and_hashes_equal_immutable_base(empty):
    from datetime import date
    from src.trading_runtime.strategy_one_protection_snapshot import project_protection_snapshot
    name='src.trading_runtime._fixed_lot_old_snapshot_control'
    control=types.ModuleType(name);control.__package__='src.trading_runtime'
    sys.modules[name]=control
    try:
        exec(compile(source('src/trading_runtime/strategy_one_protection_snapshot.py'),'<immutable-a7-snapshot-control>','exec'),control.__dict__)
        args=dict(run_id='fixed-lot-parity',session_date=date(2026,8,4),checkpoint_sequence=123,
            boundary_ms=31000,positions={} if empty else {('account','AAA','position'):opening().state})
        assert asdict(project_protection_snapshot(**args))==asdict(control.project_protection_snapshot(**args))
    finally:del sys.modules[name]


def test_manager_default_decoder_private_factor_restores_exact_immutable_function():
    from pathlib import Path
    from copy import deepcopy
    relative='src/trading_runtime/strategy_one_management_snapshot.py'
    tree=ast.parse(Path(relative).read_text(encoding='utf8'))
    baseline=ast.parse(source(relative))
    old=next(v for v in baseline.body if isinstance(v,ast.FunctionDef) and v.name=='restore_manager_snapshot')
    private=deepcopy(next(v for v in tree.body if isinstance(v,ast.FunctionDef) and v.name=='_restore_manager_snapshot_scalar'))
    private.name=old.name
    private.args.kwonlyargs=private.args.kwonlyargs[:-1];private.args.kw_defaults=private.args.kw_defaults[:-1]
    branches=[v for v in private.body if isinstance(v,ast.If) and ast.unparse(v.test)=='_selected_positions is None']
    assert len(branches)==1
    private.body=[part for v in private.body for part in (v.body if v is branches[0] else [v])]
    for call in ast.walk(private):
        if isinstance(call,ast.Call):call.keywords=[v for v in call.keywords if v.arg!='_protection_rows']
    assert ast.dump(private,include_attributes=False)==ast.dump(old,include_attributes=False)


@pytest.mark.parametrize('malformed',[False,True])
def test_manager_default_decoder_output_and_hashes_match_immutable_base(malformed):
    from dataclasses import replace
    from tests.test_strategy_one_management_snapshot import _rows
    from src.trading_runtime.strategy_one_management_snapshot import restore_manager_snapshot
    name='src.trading_runtime._fixed_lot_old_manager_control'
    control=types.ModuleType(name);control.__package__='src.trading_runtime';sys.modules[name]=control
    try:
        exec(compile(source('src/trading_runtime/strategy_one_management_snapshot.py'),'<immutable-a7-manager-control>','exec'),control.__dict__)
        rows=_rows()
        if malformed:rows=replace(rows,sources=())
        old=control.ManagerSnapshotRows(**{name:getattr(rows,name) for name in rows.__dataclass_fields__})
        if malformed:
            with pytest.raises(Exception) as expected:control.restore_manager_snapshot(old)
            with pytest.raises(type(expected.value)) as actual:restore_manager_snapshot(rows)
            assert str(actual.value)==str(expected.value)
        else:
            assert asdict(restore_manager_snapshot(rows))==asdict(control.restore_manager_snapshot(old))
    finally:del sys.modules[name]
