"""Selected resume source deltas preserve exact default cold-reader bodies."""
import ast
import copy
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
BASE='a7b47a38632cf83bab8e0fc0c165b71d7b30e35a'


@pytest.mark.parametrize('name,symbol',[
    ('strategy_one_evidence_snapshot','load_attested_evidence_snapshot'),
    ('strategy_one_broker_match_snapshot','load_attested_broker_match_snapshot'),
    ('strategy_one_campaign_snapshot','load_attested_campaign_snapshot'),
    ('strategy_one_oms_observation_snapshot','load_attested_oms_observation_snapshot')])
def test_attested_reader_default_ast_reinlines_exact_baseline(name,symbol):
    path=f'src/trading_runtime/{name}.py'
    baseline=ast.parse(subprocess.check_output(['git','show',f'{BASE}:{path}'],cwd=ROOT).decode())
    current=ast.parse((ROOT/path).read_text())
    old=next(n for n in baseline.body if isinstance(n,ast.FunctionDef) and n.name==symbol)
    new=copy.deepcopy(next(n for n in current.body if isinstance(n,ast.FunctionDef) and n.name==symbol))
    index=next(i for i,v in enumerate(new.args.kwonlyargs) if v.arg=='fixed_lot_resume')
    assert ast.literal_eval(new.args.kw_defaults[index]) is None
    new.args.kwonlyargs.pop(index);new.args.kw_defaults.pop(index)
    selected=[(i,n) for i,n in enumerate(new.body) if isinstance(n,ast.If)
        and ast.unparse(n.test)=='fixed_lot_resume is not None']
    assert len(selected)==1 and len(selected[0][1].orelse)==1
    i,branch=selected[0];new.body[i:i+1]=branch.orelse
    class DefaultRecheck(ast.NodeTransformer):
        def visit_IfExp(self,node):
            if ast.unparse(node.test)=='fixed_lot_resume is not None':return self.visit(node.orelse)
            return self.generic_visit(node)
    new=DefaultRecheck().visit(new)
    assert ast.dump(new,include_attributes=False)==ast.dump(old,include_attributes=False)


def test_selected_certification_dispatch_restores_exact_legacy_with_reviewed_classifier():
    path='src/backend/backtest_fixed_v4_certification.py'
    old=ast.parse(subprocess.check_output(['git','show',f'{BASE}:{path}'],cwd=ROOT).decode('utf-8'))
    new=ast.parse((ROOT/path).read_text(encoding='utf-8'))
    fn=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name=='certify_numbered_fixed_v4_projection')
    assert ast.unparse(fn.body[1])=='from .backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract'
    assert ast.unparse(fn.body[2])=='selected_lots = declared_fixed_structural_lot_contract(strategy_number)'
    assert isinstance(fn.body[3],ast.If) and ast.unparse(fn.body[3].test)=='selected_lots is not None'
    del fn.body[1:4]
    routes=[n for n in fn.body if isinstance(n,ast.If)
            and any(isinstance(x,ast.Assign) and ast.unparse(x.targets[0])=='legacy_gate_route' for x in n.body)]
    assert len(routes)==1
    route=routes[0]
    index=next(i for i,n in enumerate(route.body) if isinstance(n,ast.Assign)
               and ast.unparse(n.targets[0])=='legacy_gate_route')
    expected=ast.parse(('len(gates) == 2 and len(selected_gates) == 1 and '
        '(len(preliminary_gates) == 1) and (len(momentum_routes) == 1) and '
        '(preliminary_gates[0] in tuple(ast.walk(momentum_routes[0])))'),mode='eval').body
    assert ast.dump(route.body[index].value,include_attributes=False)==ast.dump(expected,include_attributes=False)
    assert len(route.body[index:])==4
    final=route.body[-1]
    assert isinstance(final,ast.If) and ast.unparse(final.test)=='not (legacy_gate_route or extracted_gate_route)'
    final.test=ast.parse('len(gates) != 2 or len(selected_gates) != 1 or len(preliminary_gates) != 1 or len(momentum_routes) != 1 or preliminary_gates[0] not in tuple(ast.walk(momentum_routes[0]))',mode='eval').body
    route.body[index:]=[final]
    original=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name==fn.name)
    assert ast.dump(fn,include_attributes=False)==ast.dump(original,include_attributes=False)
