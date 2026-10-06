"""The selected all-held failure execution graph is a closed fresh-source authority."""
import ast
from pathlib import Path

import pytest
from src.backend import backtest_strategy_sixty_six_certification as proof


def test_complete_ordered_source_seal():
    assert len(proof.REQUIRED_SOURCE_FILES) == 88
    assert tuple(proof.STRATEGY66_SOURCE_AST) == proof.REQUIRED_SOURCE_FILES
    assert len(proof.certify_strategy_sixty_six_source()) == 64


@pytest.mark.parametrize('relative', proof.REQUIRED_SOURCE_FILES)
def test_selected_authority_mutation_rejected(relative, tmp_path):
    path = Path(__file__).parents[1] / relative
    tree = ast.parse(path.read_text(encoding='utf-8'))
    expected = proof.STRATEGY66_SOURCE_AST[relative]
    if type(expected) is dict:
        name = next(iter(expected))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
        assert len(functions) == 1
        functions[0].body.insert(0, ast.Raise(exc=ast.Call(func=ast.Name(id='ValueError',ctx=ast.Load()),
            args=[ast.Constant(value='unreviewed authority')],keywords=[]),cause=None))
    else:
        tree.body.append(ast.Assign(targets=[ast.Name(id='unreviewed_authority',ctx=ast.Store())],
                                    value=ast.Constant(value=True)))
    mutant = tmp_path / 'mutation.py'
    text = ast.unparse(ast.fix_missing_locations(tree))
    assert text != ast.unparse(ast.parse(path.read_text(encoding='utf-8')))
    mutant.write_text(text,encoding='utf-8')
    with pytest.raises(ValueError,match='pinned source changed'):
        proof.certify_strategy_sixty_six_source(source_overrides={relative:mutant})


def test_wholly_post_held_source_guard_mutation_rejected(tmp_path):
    relative='src/trading_runtime/all_held_original_risk_failure.py'
    source=(Path(__file__).parents[1]/relative).read_text(encoding='utf-8')
    old='value.boundary_ms - 5_000 < value.first_held_boundary_ms'
    assert source.count(old)==1
    changed=source.replace(old,'value.boundary_ms < value.first_held_boundary_ms')
    assert changed!=source
    mutant=tmp_path/'forming_guard.py'
    mutant.write_text(changed,encoding='utf-8')
    with pytest.raises(ValueError,match='pinned source changed'):
        proof.certify_strategy_sixty_six_source(source_overrides={relative:mutant})


@pytest.mark.parametrize('defect', ['missing','extra','wrong_leaf_dict','missing_symbol','extra_symbol'])
def test_closed_metadata_shape_rejects(monkeypatch,defect):
    from copy import deepcopy
    values=deepcopy(proof.STRATEGY66_SOURCE_AST)
    if defect == 'missing':values.pop(proof.REQUIRED_SOURCE_FILES[-1])
    elif defect == 'extra':values['unknown.py']='a'*64
    elif defect == 'wrong_leaf_dict':values[proof.REQUIRED_SOURCE_FILES[0]]={'__module__':'a'*64}
    else:
        symbols=values['src/backend/backtest_fixed_v4_certification.py']
        if defect == 'missing_symbol':symbols.clear()
        else:symbols['unknown']='a'*64
    monkeypatch.setattr(proof,'STRATEGY66_SOURCE_AST',values)
    with pytest.raises(ValueError,match='not sealed|shape changed'):
        proof.certify_strategy_sixty_six_source()


def test_unknown_override_rejects_before_read(tmp_path):
    with pytest.raises(ValueError,match='outside complete authority'):
        proof.certify_strategy_sixty_six_source(source_overrides={'unknown.py':tmp_path/'absent'})


def test_fresh_own_declaration_tamper_rejected(tmp_path):
    relative='src/backend/backtest_strategy_sixty_six_certification.py'
    path=Path(__file__).parents[1]/relative
    source=path.read_text(encoding='utf-8')
    tree=ast.parse(source)
    nodes=[n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1
           and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='REQUIRED_SOURCE_FILES']
    assert len(nodes)==1
    nodes[0].value=ast.Tuple(elts=[ast.Constant(value='foreign.py')],ctx=ast.Load())
    mutant=tmp_path/'metadata.py'
    mutant.write_text(ast.unparse(ast.fix_missing_locations(tree)),encoding='utf-8')
    with pytest.raises(ValueError,match='fresh source declarations differ'):
        proof.certify_strategy_sixty_six_source(source_overrides={relative:mutant})


@pytest.mark.parametrize('defect',['global','import','changed_import','docstring','extra_function'])
def test_own_module_envelope_rejects_outside_symbol_changes(tmp_path,defect):
    relative='src/backend/backtest_strategy_sixty_six_certification.py'
    source=(Path(__file__).parents[1]/relative).read_text(encoding='utf-8')
    if defect=='global':mutant_source=source+'\nforeign_authority = True\n'
    elif defect=='import':mutant_source=source+'\nimport os\n'
    elif defect=='extra_function':mutant_source=source+'\ndef foreign_authority():\n    return True\n'
    elif defect=='docstring':
        old='Closed selected all-held failure source authority; publication is separate.'
        assert source.count(old)==2
        mutant_source=source.replace(old,'foreign source authority',1)
    else:
        old='from hashlib import sha256'
        assert source.count(old)==2
        mutant_source=source.replace(old,'from hashlib import sha1 as sha256',1)
    assert mutant_source!=source
    mutant=tmp_path/'module_envelope.py'
    mutant.write_text(mutant_source,encoding='utf-8')
    with pytest.raises(ValueError,match='own source module envelope changed'):
        proof.certify_strategy_sixty_six_source(source_overrides={relative:mutant})
