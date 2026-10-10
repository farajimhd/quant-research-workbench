"""Exact retained-parent restoration for declared saved preparation routing."""
import ast
from hashlib import sha256
import json
from pathlib import Path
REVIEWED_EDITS = {'backend/backtest_fixed_v4_certification.py': {'current_ast': 'ebc205dacf19bcf018473d636d931967aa753a4ebf4d3573958ecc62da9a2876',
                                                'parent_ast': 'c6666dd1e8901454271db97ac3b430ddcc4fd7ee9c3e548d90f15055522d629b',
                                                'edits': [('def _reviewed_fixed_lot_ast_recipe(source: str, '
                                                           'relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v29 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v29\n'
                                                           '    source = restore_v29(source, relative)\n',
                                                           'def _reviewed_fixed_lot_ast_recipe(source: str, '
                                                           'relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'),
                                                          ('def _reviewed_fixed_lot_core_projection(source: '
                                                           'str, relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Retain core source pins under the same '
                                                           'bounded, independently reviewed projection."""\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v29 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v29\n'
                                                           '    source = restore_v29(source, relative)\n',
                                                           'def _reviewed_fixed_lot_core_projection(source: '
                                                           'str, relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Retain core source pins under the same '
                                                           'bounded, independently reviewed '
                                                           'projection."""\n')]},
 'backend/backtest_v4_saved_review.py': {'current_ast': 'e100150ef2e6c60d49b6dc6e058a10bae9ecc425676fe5e5c7fe5cd701132be6',
                                         'parent_ast': '0383859e6f9cae8f269687f9e5f4e9775d6586e5ec63d482c70646b5adf68e3d',
                                         'edits': [('        from '
                                                    '.backtest_declared_fixed_lot_saved_preparation import '
                                                    'prepare_declared_saved_fixed_lot_session as '
                                                    'prepare_fixed_structural_lot_session\n',
                                                    '        from '
                                                    '.backtest_fixed_structural_lot_execution_v13 import '
                                                    'prepare_fixed_structural_lot_session\n')]}}
APPROVED_METADATA_ANCHOR = '71b2fabcbb608505ece6d3b5a19b5791f71409769d78ea089f8482ae80ffdf38'
APPROVED_SELF_AST = 'cf6bc331c9bbcdbab705d2daf52d0197d1721990900605d31f7c4af46f2f7587'

def restore_reviewed_parent_source(source, relative):
    own = Path(__file__)
    fresh_source = own.read_text(encoding='utf-8')
    tree = ast.parse(fresh_source)
    names = ('REVIEWED_EDITS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST')
    fresh = {}
    for name in names:
        declarations = [n for n in tree.body if type(n) is ast.Assign and len(n.targets) == 1 and (type(n.targets[0]) is ast.Name) and (n.targets[0].id == name)]
        if len(declarations) != 1:
            raise ValueError('Declared saved-preparation compatibility declaration shape differs')
        fresh[name] = ast.literal_eval(declarations[0].value)
    if fresh != dict(zip(names, (REVIEWED_EDITS, APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST))):
        raise ValueError('Declared saved-preparation compatibility loaded and fresh metadata differs')
    for node in tree.body:
        if type(node) is ast.Assign and len(node.targets) == 1 and (type(node.targets[0]) is ast.Name):
            if node.targets[0].id in names:
                node.value = ast.Constant(None)
    if sha256(ast.unparse(tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Declared saved-preparation compatibility envelope differs')
    if sha256(json.dumps(REVIEWED_EDITS, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != APPROVED_METADATA_ANCHOR:
        raise ValueError('Declared saved-preparation compatibility metadata anchor differs')
    recipe = REVIEWED_EDITS.get(relative.removeprefix('src/'))
    if recipe is None:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Declared saved-preparation compatibility changed during restoration')
        return source
    if sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() != recipe['current_ast']:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Declared saved-preparation compatibility changed during restoration')
        return source
    restored = source
    for current, previous in recipe['edits']:
        if not current or restored.count(current) != 1:
            raise ValueError('Declared saved-preparation compatibility exact reviewed edit differs: ' + relative)
        restored = restored.replace(current, previous, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != recipe['parent_ast']:
        raise ValueError('Declared saved-preparation compatibility retained parent AST differs: ' + relative)
    if own.read_text(encoding='utf-8') != fresh_source:
        raise ValueError('Declared saved-preparation compatibility changed during restoration')
    return restored
