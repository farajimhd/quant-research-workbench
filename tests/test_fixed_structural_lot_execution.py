"""Read-only preparation parity and operation-local identity controls.

Controlled preparation tests do not grant an installed financial authority.
"""
import ast
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from subprocess import check_output
from types import SimpleNamespace

import pytest

from src.backend import backtest_strategy_one_execution as execution
from src.backend import backtest_fixed_structural_lot_execution as selected


def test_extracted_inherited_gate_pipeline_has_exact_baseline_statement_ast():
    original=ast.parse(check_output(['git','show',
        'a7b47a38632cf83bab8e0fc0c165b71d7b30e35a:src/backend/backtest_strategy_one_execution.py'],text=True))
    function=next(v for v in original.body if isinstance(v,ast.AsyncFunctionDef)
        and v.name=='run_certified_strategy_one_session')
    start=next(i for i,v in enumerate(function.body) if isinstance(v,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='cost_rejections' for t in v.targets))
    end=next(i for i,v in enumerate(function.body) if isinstance(v,ast.Assign)
        and any(isinstance(t,ast.Tuple) and any(isinstance(n,ast.Name) and n.id=='survivors' for n in t.elts)
            for t in v.targets))
    class Rebind(ast.NodeTransformer):
        def visit_Attribute(self,node):
            if ast.unparse(node)=='runtime.config.strategy_revision':return ast.Name('strategy_number',ast.Load())
            if ast.unparse(node)=='runtime.run_id':return ast.Name('run_id',ast.Load())
            return self.generic_visit(node)
        def visit_If(self,node):
            if ast.unparse(node.test)=='not callable(first_price_ready)':return None
            return self.generic_visit(node)
        def visit_Expr(self,node):
            if isinstance(node.value,ast.Call) and ast.unparse(node.value.func) in (
                    'runtime.bind_strategy_one_price_source','first_price_ready'):return None
            return self.generic_visit(node)
    expected=Rebind().visit(ast.Module(deepcopy(function.body[start:end]),[]))
    current=ast.parse(Path(execution.__file__).read_text())
    helper=next(v for v in current.body if isinstance(v,ast.FunctionDef)
        and v.name=='prepare_strategy_one_entry_authorities')
    actual=ast.Module(helper.body[3:-1],[])
    assert ast.dump(actual,include_attributes=False)==ast.dump(expected,include_attributes=False)


def test_default_readonly_gate_output_and_no_client_calls(monkeypatch):
    visible,gate=object(),object()
    calls=[]
    monkeypatch.setattr(execution,'project_candidate_plan',lambda value,**kw:calls.append(('project',value,kw)) or visible)
    monkeypatch.setattr(execution,'compile_static_entry_gate',lambda *args,**kw:calls.append(('gate',args,kw)) or gate)
    result=execution.prepare_strategy_one_entry_authorities(market=object(),candidates='candidate',
        entry='entry',through_boundary_ms=100,strategy_number=1,run_id='run',
        client_factory=lambda:pytest.fail('Unneeded market reader'))
    assert result==(visible,gate,None,None,None,(),None)
    assert calls==[('project','candidate',{'through_boundary_ms':100}),
        ('gate',(visible,'entry'),{'strategy_number':1,'momentum_plan':None,'initial_momentum_plan':None})]


def test_fabricated_session_does_not_issue_writer_or_execution_authority():
    fake=selected.PreparedFixedStructuralLotSession(object(),(),object())
    with pytest.raises(ValueError,match='factory-issued'):
        fake.require(market=object(),candidates=object(),entry=object(),
            through_boundary_ms=100,run_id='run',number=77)
    with pytest.raises(ValueError,match='factory-issued'):
        fake.bind_runtime(object())


def test_empty_horizon_requires_real_empty_factory_before_profile_issuance(monkeypatch):
    from src.backend import backtest_strategy_one_candidate_store as candidate_module
    monkeypatch.setattr(candidate_module,'project_candidate_plan',lambda *a,**kw:SimpleNamespace(prepared=()))
    monkeypatch.setattr(execution,'prepare_strategy_one_entry_authorities',
        lambda **kw:pytest.fail('An empty source must not be fabricated'))
    from src.backend import backtest_fixed_structural_lot_empty as empty
    def missing_source(*a,**kw):raise ValueError('Missing genuine empty source authority')
    monkeypatch.setattr(empty,'prepare_empty_fixed_structural_lot_source',missing_source)
    with pytest.raises(ValueError,match='genuine empty source'):
        selected.prepare_fixed_structural_lot_session(number=77,run_id='run',session_date=object(),
            market=object(),candidates=object(),entry=None,seeds=None,through_boundary_ms=100,
            client_factory=lambda:SimpleNamespace(close=lambda:None))


def test_actual_replay_manager_ready_passes_keeper_not_session_date(monkeypatch):
    """Execute the actual nested caller; no substituted production callback."""
    import asyncio
    from datetime import date
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.trading_runtime.strategy_one_management_snapshot import ManagedManagerSnapshotHeadReader
    from tests.test_arte_typed_insert_dispatch import Keeper
    keeper=Keeper();keeper.add_listener=lambda listener:None
    keeper.connected=True;keeper.client_id=(101,b'fixture')
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    calls=[]
    owner=object();packet=object()
    monkeypatch.setattr(selected,'bind_fixed_structural_lot_manager',
        lambda prepared,**kw:calls.append(('bind',prepared,kw['session'])) or owner)
    async def restore(actual_owner,manager,keeper_session):
        assert actual_owner is owner and keeper_session is session
        # This instantiates the real type gate that rejects a date argument.
        ManagedManagerSnapshotHeadReader(keeper_session)
        calls.append(('restore',keeper_session))
    monkeypatch.setattr(bootstrap,'restore_fixed_structural_lot_native_manager',restore)
    root=Path(execution.__file__).parent
    tree=ast.parse((root/'replay_run_service.py').read_text())
    method=next(v for v in ast.walk(tree) if isinstance(v,ast.AsyncFunctionDef)
        and v.name=='_run_strategy_one_fixed_days')
    callback=next(v for v in method.body if isinstance(v,ast.AsyncFunctionDef) and v.name=='manager_ready')
    scope=dict(__name__='src.backend.replay_run_service',__package__='src.backend',
        self=SimpleNamespace(_strategy_one_manager=None,_fixed_structural_lot_session=packet,
        _journal_publisher=object(),_fixed_keeper_session=session,
        definition=SimpleNamespace(session_date=date(2026,8,18))),fixed_restore=object(),start_after=100)
    exec(compile(ast.fix_missing_locations(ast.Module([deepcopy(callback)],[])),str(root/'replay_run_service.py'),'exec'),scope)
    manager=SimpleNamespace(contract=SimpleNamespace(confirmed_original_risk_policy=None))
    asyncio.run(scope['manager_ready'](manager))
    assert scope['self']._strategy_one_manager is manager
    assert calls==[('bind',packet,date(2026,8,18)),('restore',session)]


@pytest.mark.parametrize('change',['copy','market','candidates','entry','cursor','run','number','operation','authorities','profile'])
def test_session_identity_is_operation_local_not_value_or_hash_authority(monkeypatch,change):
    # This controlled issuance seam tests identity enforcement only. Authentic
    # installation is covered separately and remains closed without a seal.
    from src.trading_runtime import fixed_structural_lot_profile as profile_module
    market,candidates,entry=object(),object(),object()
    source=SimpleNamespace(require_installed_admission=lambda:None)
    operation=SimpleNamespace(source=source)
    authorities=(object(),)
    profile=SimpleNamespace(operation=operation)
    packet=selected.PreparedFixedStructuralLotSession(operation,authorities,profile)
    selected._SESSIONS[packet]=(market,candidates,entry,100,'run',77,operation,authorities,profile)
    monkeypatch.setattr(profile_module,'require_fixed_structural_lot_profile',lambda p:p)
    kwargs=dict(market=market,candidates=candidates,entry=entry,through_boundary_ms=100,run_id='run',number=77)
    assert packet.require(**kwargs) is packet
    if change=='copy':packet=replace(packet)
    elif change in ('market','candidates','entry'):kwargs[change]=object()
    elif change=='cursor':kwargs['through_boundary_ms']=200
    elif change=='run':kwargs['run_id']='foreign'
    elif change=='number':kwargs['number']=78
    elif change=='authorities':packet=replace(packet,entry_authorities=tuple([object()]))
    else:packet=replace(packet,**{change:object()})
    with pytest.raises(ValueError,match='source preparation|source operation'):
        packet.require(**kwargs)
