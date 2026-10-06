"""Exact schema extraction parity and genuinely cold writer import order."""
import ast
from dataclasses import asdict
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.trading_runtime.arte_journal_schema import TableContract

ROOT=Path(__file__).resolve().parents[1]
BASE='9c1e3fbaad91145483c10d44ba2399e31afabb4b'
PAIRS=(('arte_declared_native_command_v4','arte_declared_native_entry_schema',5),
       ('arte_declared_native_management_v4','arte_declared_native_management_schema',9))


def original(name):
    return ast.parse(subprocess.check_output(['git','show',f'{BASE}:src/trading_runtime/{name}.py'],cwd=ROOT))


def schema_nodes(tree):
    first=next(i for i,n in enumerate(tree.body) if isinstance(n,ast.Assign) and
               any(isinstance(t,ast.Name) and t.id=='CONTRACT' for t in n.targets))
    last=next(i for i,n in enumerate(tree.body) if isinstance(n,ast.Assign) and
              any(isinstance(t,ast.Name) and t.id=='TABLES' for t in n.targets))
    return tree.body[first:last+1]


@pytest.mark.parametrize('old,new,count',PAIRS)
def test_whole_original_module_ast_restores_exactly(old,new,count):
    baseline=original(old)
    current=ast.parse((ROOT/'src/trading_runtime'/f'{old}.py').read_bytes())
    extracted=ast.parse((ROOT/'src/trading_runtime'/f'{new}.py').read_bytes())
    replacements=schema_nodes(extracted)
    body=[]; imports=0
    for node in current.body:
        if isinstance(node,ast.ImportFrom) and node.module==new:
            imports+=1; body.extend(replacements)
        else: body.append(node)
    assert imports==1
    current.body=body
    assert ast.dump(current,include_attributes=False)==ast.dump(baseline,include_attributes=False)
    assert ast.dump(ast.Module(body=replacements,type_ignores=[]),include_attributes=False)==ast.dump(
        ast.Module(body=schema_nodes(baseline),type_ignores=[]),include_attributes=False)


@pytest.mark.parametrize('old,new,count',PAIRS)
def test_every_contract_payload_ddl_and_private_alias_unchanged(old,new,count):
    baseline={"TableContract":TableContract}
    exec(compile(ast.Module(body=schema_nodes(original(old)),type_ignores=[]),'baseline-schema','exec'),baseline)
    extracted=importlib.import_module('src.trading_runtime.'+new)
    original_api=importlib.import_module('src.trading_runtime.'+old)
    assert len(extracted.TABLES)==count
    for before,after in zip(baseline['TABLES'],extracted.TABLES,strict=True):
        assert asdict(before)==asdict(after)
        assert before.ddl()==after.ddl()
        assert "storage_policy = 'live_market_ssd'" in after.ddl()
    for name,value in baseline.items():
        if name in ('__builtins__','TableContract'): continue
        assert getattr(original_api,name) is getattr(extracted,name)
        if name!='_table': assert getattr(extracted,name)==value
    assert asdict(extracted._table('fixture',(('field','UInt8'),)))==asdict(baseline['_table']('fixture',(('field','UInt8'),)))


@pytest.mark.parametrize('old,new,count',PAIRS)
def test_standalone_schema_has_only_shared_contract_dependency(old,new,count):
    tree=ast.parse((ROOT/'src/trading_runtime'/f'{new}.py').read_bytes())
    imports=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom))]
    assert len(imports)==1
    assert isinstance(imports[0],ast.ImportFrom)
    assert imports[0].module=='arte_journal_schema' and imports[0].level==1
    assert tuple(a.name for a in imports[0].names)==('TableContract',)


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('writer_first',[False,True])
def test_cold_schema_writer_import_orders_no_cycle(reverse,writer_first):
    schemas=[p[1] for p in PAIRS]
    if reverse: schemas.reverse()
    code='import importlib,sys\n'
    if writer_first: code+="importlib.import_module('src.trading_runtime.arte_journal_writer')\n"
    code+='schemas='+repr(schemas)+'\n'
    code+="mods=[importlib.import_module('src.trading_runtime.'+name) for name in schemas]\n"
    if not writer_first:
        code+="assert 'src.trading_runtime.arte_journal_writer' not in sys.modules\n"
        code+="assert 'src.trading_runtime.arte_declared_native_command_v4' not in sys.modules\n"
        code+="assert 'src.trading_runtime.arte_declared_native_management_v4' not in sys.modules\n"
        code+="importlib.import_module('src.trading_runtime.arte_journal_writer')\n"
    code+="assert sorted(len(m.TABLES) for m in mods)==[5,9]\nprint('cold import order passed')\n"
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='cold import order passed'
