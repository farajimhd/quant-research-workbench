"""Exact reviewed publication-source successor restoration to retained110."""
import ast
from hashlib import sha256
import json
from pathlib import Path
REVIEWED_EDITS = {'trading_runtime/strategy_registry.py': {'current_ast': '6c5320265f9f677233aa5b32a3c119ac74232087d9a4039910c0c82dd7dd1b31',
                                          'parent_ast': '07081bb695eee22e3248beb833121820fb2bed5fb77190bd87b365c4dd45d2d2',
                                          'edits': (('\n'
                                                     '\n'
                                                     'def numbered_strategy(number: int) -> '
                                                     'NumberedStrategyRelease:\n'
                                                     '    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, '
                                                     '13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, '
                                                     '26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, '
                                                     '39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                     '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, 68, 69, 70, '
                                                     '71, 72, 73, 74, 77, 80, 81, 82, 83, 84, 85, 86, 87, '
                                                     '88, 89, 90, 92, 93, 94, 95, 97, 98, 99, 101, 109, 110, '
                                                     '111, 112):\n'
                                                     '        initialize_numbered_fixed_strategies()\n'
                                                     '    with _LOCK:\n'
                                                     '        release = _NUMBERED_RELEASES.get(number)\n',
                                                     '\n'
                                                     '\n'
                                                     'def numbered_strategy(number: int) -> '
                                                     'NumberedStrategyRelease:\n'
                                                     '    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, '
                                                     '13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, '
                                                     '26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, '
                                                     '39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                     '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, 68, 69, 70, '
                                                     '71, 72, 73, 74, 77, 80, 81, 82, 83, 84, 85, 86, 87, '
                                                     '88, 89, 90, 92, 93, 94, 95, 97, 98, 99, 101, 109, 110, '
                                                     '111):\n'
                                                     '        initialize_numbered_fixed_strategies()\n'
                                                     '    with _LOCK:\n'
                                                     '        release = _NUMBERED_RELEASES.get(number)\n'),
                                                    ('                '
                                                     'derive_strategy_one_hundred_eleven_configuration,verify_prepared_strategy_one_hundred_eleven_configuration,\n'
                                                     '                '
                                                     'certify_publication_reuse_source,management_parent_release)))\n'
                                                     '        register_numbered_strategy(publication_reuse)\n'
                                                     '        from .strategy_one_hundred_twelve_release '
                                                     'import (release_contract as context_capacity_release,\n'
                                                     '            '
                                                     'derive_strategy_one_hundred_twelve_configuration, '
                                                     'verify_prepared_strategy_one_hundred_twelve_configuration)\n'
                                                     '        from .strategy_one_hundred_twelve_contract '
                                                     'import strategy_one_hundred_twelve_contract\n'
                                                     '        from '
                                                     'src.backend.backtest_fixed_structural_lot_certification_v31 '
                                                     'import certify_fixed_structural_lot_source as '
                                                     'certify_context_capacity_source\n'
                                                     '        context_capacity = context_capacity_release()\n'
                                                     '        '
                                                     'register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n'
                                                     '            '
                                                     'strategy_id=context_capacity.executor_strategy_id, '
                                                     'revision=context_capacity.executor_revision,\n'
                                                     '            '
                                                     'evaluation_interval=context_capacity.evaluation_interval, '
                                                     'strategy_factory=_strategy_two_factory,\n'
                                                     '            '
                                                     'contract_factory=strategy_one_hundred_twelve_contract,\n'
                                                     '            '
                                                     'manifest_authority=NativeManifestAuthority(42, '
                                                     "'fixed-structural-lots-from',\n"
                                                     '                '
                                                     'derive_strategy_one_hundred_twelve_configuration, '
                                                     'verify_prepared_strategy_one_hundred_twelve_configuration,\n'
                                                     '                certify_context_capacity_source, '
                                                     'management_parent_release)))\n'
                                                     '        register_numbered_strategy(context_capacity)\n'
                                                     '        _NUMBERED_FIXED_REGISTERED = True\n'
                                                     '\n'
                                                     '\n',
                                                     '                '
                                                     'derive_strategy_one_hundred_eleven_configuration,verify_prepared_strategy_one_hundred_eleven_configuration,\n'
                                                     '                '
                                                     'certify_publication_reuse_source,management_parent_release)))\n'
                                                     '        register_numbered_strategy(publication_reuse)\n'
                                                     '        _NUMBERED_FIXED_REGISTERED = True\n'
                                                     '\n'
                                                     '\n'),
                                                    ('SELECTED_CHECKPOINT_PRODUCT_RULE = '
                                                     "'immutable-selected-checkpoint-products@1'\n"
                                                     '\n'
                                                     'OPERATION_CHECKPOINT_READER_RULE = '
                                                     "'immutable-operation-bound-checkpoint-reader@1'\n"
                                                     '\n',
                                                     'SELECTED_CHECKPOINT_PRODUCT_RULE = '
                                                     "'immutable-selected-checkpoint-products@1'\n"
                                                     '\n'
                                                     'OPERATION_CHECKPOINT_READER_RULE = '
                                                     "'immutable-operation-bound-checkpoint-reader@1'\n"))},
 'backend/backtest_fixed_v4_certification.py': {'current_ast': '7913e74fae450e4b937acebf6eea9bb5455de6f72e24dcc22946c67d12636e77',
                                                'parent_ast': '9054becc64fb937b338fba777160eb9a98f2213ca0fa3ca4a60dd1a740f445a5',
                                                'edits': (('def _reviewed_fixed_lot_ast_recipe(source: str, '
                                                           'relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'
                                                           '    supplied_source = source\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v31 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v31\n'
                                                           '    source = restore_v31(source, relative)\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v30 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v30\n'
                                                           '    source = restore_v30(source, relative)\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v29 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v29\n',
                                                           'def _reviewed_fixed_lot_ast_recipe(source: str, '
                                                           'relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'
                                                           '    supplied_source = source\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v30 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v30\n'
                                                           '    source = restore_v30(source, relative)\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v29 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v29\n'),
                                                          ('def _reviewed_fixed_lot_core_projection(source: '
                                                           'str, relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Retain core source pins under the same '
                                                           'bounded, independently reviewed projection."""\n'
                                                           '    supplied_source = source\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v31 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v31\n'
                                                           '    source = restore_v31(source, relative)\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v30 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v30\n'
                                                           '    source = restore_v30(source, relative)\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v29 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v29\n',
                                                           'def _reviewed_fixed_lot_core_projection(source: '
                                                           'str, relative: str, name: str, expected: str) -> '
                                                           'bool:\n'
                                                           '    """Retain core source pins under the same '
                                                           'bounded, independently reviewed projection."""\n'
                                                           '    supplied_source = source\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v30 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v30\n'
                                                           '    source = restore_v30(source, relative)\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v29 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v29\n'),
                                                          ('                raise ValueError("Numbered '
                                                           'session-exit reviewed authority changed: " + '
                                                           'relative + ":" + name)\n'
                                                           '        observed.append((relative, '
                                                           'sha256(source.encode()).hexdigest()))\n'
                                                           '    return sha256(json.dumps(observed, '
                                                           'separators=(",", ":")).encode()).hexdigest()\n'
                                                           '\n',
                                                           '                raise ValueError("Numbered '
                                                           'session-exit reviewed authority changed: " + '
                                                           'relative + ":" + name)\n'
                                                           '        observed.append((relative, '
                                                           'sha256(source.encode()).hexdigest()))\n'
                                                           '    return sha256(json.dumps(observed, '
                                                           'separators=(",", '
                                                           '":")).encode()).hexdigest()\n'))}}
APPROVED_METADATA_ANCHOR = 'bf6ec69fd06070b025b0f71c7dac9127b30f25abf41d99ee858fae85337f5235'
APPROVED_SELF_AST = 'bb5a66c857668375f096e517981409bcb2397bb2c510119cf9fffdf53dccf1b0'

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
