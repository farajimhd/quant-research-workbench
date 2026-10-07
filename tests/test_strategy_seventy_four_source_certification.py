"""Fresh closed source proofs for the declared PM replacement successor."""
import ast
from copy import deepcopy
from pathlib import Path

import pytest

from src.backend import backtest_strategy_seventy_four_certification as proof


def test_complete_ordered_source_seal():
    assert len(proof.REQUIRED_SOURCE_FILES) == 113
    assert tuple(proof.STRATEGY74_SOURCE_AST) == proof.REQUIRED_SOURCE_FILES
    assert len(proof.certify_strategy_seventy_four_source()) == 64


@pytest.mark.parametrize('relative', proof.REQUIRED_SOURCE_FILES)
def test_each_actual_source_leaf_mutation_rejected(relative, tmp_path):
    tree = ast.parse((Path(__file__).parents[1] / relative).read_text(encoding='utf-8'))
    expected = proof.STRATEGY74_SOURCE_AST[relative]
    if type(expected) is dict:
        name = next(iter(expected))
        node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
        node.body.insert(0, ast.Raise(exc=ast.Call(func=ast.Name(id='ValueError', ctx=ast.Load()),
            args=[ast.Constant(value='unreviewed authority')], keywords=[]), cause=None))
    else:
        tree.body.append(ast.Assign(targets=[ast.Name(id='unreviewed_authority', ctx=ast.Store())],
                                    value=ast.Constant(value=True)))
    mutant = tmp_path / 'mutation.py'
    mutant.write_text(ast.unparse(ast.fix_missing_locations(tree)), encoding='utf-8')
    with pytest.raises(ValueError, match='pinned source changed') as caught:
        proof.certify_strategy_seventy_four_source(source_overrides={relative: mutant})
    assert relative in str(caught.value)


@pytest.mark.parametrize('defect', ['missing', 'extra', 'wrong_leaf_dict', 'missing_symbol', 'extra_symbol'])
def test_closed_metadata_shape_rejects(monkeypatch, defect):
    values = deepcopy(proof.STRATEGY74_SOURCE_AST)
    symbol_path = 'src/backend/backtest_strategy_seventy_four_certification.py'
    if defect == 'missing':
        values.pop(proof.REQUIRED_SOURCE_FILES[-1])
    elif defect == 'extra':
        values['unknown.py'] = 'a' * 64
    elif defect == 'wrong_leaf_dict':
        values[proof.REQUIRED_SOURCE_FILES[0]] = {'__module__': 'a' * 64}
    elif defect == 'missing_symbol':
        values[symbol_path] = {}
    else:
        values[symbol_path]['foreign'] = 'a' * 64
    monkeypatch.setattr(proof, 'STRATEGY74_SOURCE_AST', values)
    with pytest.raises(ValueError):
        proof.certify_strategy_seventy_four_source()


def test_unknown_override_rejects(tmp_path):
    with pytest.raises(ValueError, match='outside complete authority'):
        proof.certify_strategy_seventy_four_source(source_overrides={'foreign.py': tmp_path / 'unknown'})


@pytest.mark.parametrize('declaration', ['REQUIRED_SOURCE_FILES', 'STRATEGY74_SOURCE_AST'])
def test_fresh_self_metadata_drift_rejects(declaration, tmp_path):
    relative = 'src/backend/backtest_strategy_seventy_four_certification.py'
    tree = ast.parse((Path(__file__).parents[1] / relative).read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == declaration)
    if declaration == 'REQUIRED_SOURCE_FILES':
        node.value.elts.pop()
    else:
        node.value.values[0] = ast.Constant(value='a' * 64)
    mutant = tmp_path / 'fresh_metadata.py'
    mutant.write_text(ast.unparse(ast.fix_missing_locations(tree)), encoding='utf-8')
    with pytest.raises(ValueError, match='fresh source declarations differ'):
        proof.certify_strategy_seventy_four_source(source_overrides={relative: mutant})


def test_warm_control_then_fresh_helper_mutation_then_restored_control(tmp_path):
    original = proof.certify_strategy_seventy_four_source()
    relative = 'src/trading_runtime/premarket_confirmed_original_risk.py'
    source = (Path(__file__).parents[1] / relative).read_text(encoding='utf-8')
    mutant = tmp_path / 'warm_helper.py'
    mutant.write_text(source + '\nunreviewed_authority = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned source changed'):
        proof.certify_strategy_seventy_four_source(source_overrides={relative: mutant})
    assert proof.certify_strategy_seventy_four_source() == original
