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
                                                    'prepare_fixed_structural_lot_session\n')]},
 'backend/historical_runtime_versions.py': {'current_ast': 'df2248b1a36f60fd88898a4de5f541243c62c4635e595ed5f2bf69318db32438',
                                            'parent_ast': '96941bdcb6b84439c3ba3f0d1fc0deb9cde238746088d1b284284aa0969857ec',
                                            'edits': [('@lru_cache(maxsize=2)\n'
                                                       'def _numbered_projection_for_code(certificate_fn: '
                                                       'Any, implementation_code: Any,\n'
                                                       '                                 source_fingerprint: '
                                                       'str, strategy_number: int) -> str:\n'
                                                       '    """Retain only a result issued by this exact '
                                                       'callback implementation."""\n'
                                                       '    if source_fingerprint != '
                                                       'LOADED_BACKEND_FINGERPRINT:\n'
                                                       '        raise RuntimeError("Backend source changed '
                                                       'after startup")\n'
                                                       "    if getattr(certificate_fn, '__code__', None) is "
                                                       'not implementation_code:\n'
                                                       '        raise RuntimeError("Numbered projection '
                                                       'callback code changed")\n'
                                                       '    result = certificate_fn(strategy_number)\n'
                                                       "    if getattr(certificate_fn, '__code__', None) is "
                                                       'not implementation_code:\n'
                                                       '        raise RuntimeError("Numbered projection '
                                                       'callback code changed during proof")\n'
                                                       '    return result\n'
                                                       '\n'
                                                       '\n'
                                                       'def _loaded_numbered_projection(certificate_fn: Any, '
                                                       'source_fingerprint: str,\n'
                                                       '                               strategy_number: int) '
                                                       '-> str:\n'
                                                       '    """A changed callback body cannot inherit its '
                                                       'previous cached proof.\n'
                                                       '\n'
                                                       '    This guards callback identity only. Callers '
                                                       'still own complete dependency,\n'
                                                       '    source approval, release and configuration '
                                                       'checks.\n'
                                                       '    """\n'
                                                       '    implementation_code = getattr(certificate_fn, '
                                                       "'__code__', None)\n"
                                                       '    if implementation_code is None:\n'
                                                       '        raise ValueError("Numbered projection '
                                                       'requires a Python source callback")\n'
                                                       '    if source_fingerprint != '
                                                       'LOADED_BACKEND_FINGERPRINT:\n'
                                                       '        raise RuntimeError("Backend source changed '
                                                       'after startup")\n'
                                                       '    result = '
                                                       '_numbered_projection_for_code(certificate_fn, '
                                                       'implementation_code,\n'
                                                       '                                         '
                                                       'source_fingerprint, strategy_number)\n'
                                                       "    if (getattr(certificate_fn, '__code__', None) is "
                                                       'not implementation_code\n'
                                                       '            or source_fingerprint != '
                                                       'LOADED_BACKEND_FINGERPRINT):\n'
                                                       '        raise RuntimeError("Numbered projection '
                                                       'implementation changed during lookup")\n'
                                                       '    return result\n'
                                                       '\n'
                                                       '\n',
                                                       '@lru_cache(maxsize=2)\n'
                                                       'def _loaded_numbered_projection(certificate_fn: Any, '
                                                       'source_fingerprint: str,\n'
                                                       '                               strategy_number: int) '
                                                       '-> str:\n'
                                                       '    """A different numbered capability requires its '
                                                       'own source-bound proof."""\n'
                                                       '    if source_fingerprint != '
                                                       'LOADED_BACKEND_FINGERPRINT:\n'
                                                       '        raise RuntimeError("Backend source changed '
                                                       'after startup")\n'
                                                       '    return certificate_fn(strategy_number)\n'
                                                       '\n'
                                                       '\n')]},
 'backend/backtest_fixed_structural_lot_native_v20.py': {'current_ast': 'c8c0639e64075f074c0b3c73de5b191574731b808053fbbacb5714ad1eed2e82',
                                                         'parent_ast': '8f8f83f67ccf61bdc165fd47d4f80794e57ba8327a6d730e8443bbeac4fdaabb',
                                                         'edits': [('    from '
                                                                    '.backtest_native_complete_projection_reuse '
                                                                    'import '
                                                                    'load_complete_installed_projection\n'
                                                                    '    proof = '
                                                                    'load_complete_installed_projection(own)\n',
                                                                    '    from '
                                                                    '.backtest_fixed_v4_certification import '
                                                                    'certify_numbered_fixed_v4_projection\n'
                                                                    '    proof = '
                                                                    'certify_numbered_fixed_v4_projection(number)\n')]},
 'trading_runtime/strategy_registry.py': {'current_ast': '45fde19ea03516405f3d99e4b80df5cd4576b59461c25e19399241d01651d71c',
                                          'parent_ast': 'f5f34c7df072f50d664db8a22c62bea5b8fce7db1689eea42536248ef99ca65e',
                                          'edits': [(', 101, 109, 110):', ', 101, 109):'),
                                                    ('        from .strategy_one_hundred_ten_release import '
                                                     '(release_contract as saved_native_release,\n'
                                                     '            '
                                                     'derive_strategy_one_hundred_ten_configuration,verify_prepared_strategy_one_hundred_ten_configuration)\n'
                                                     '        from .strategy_one_hundred_ten_contract import '
                                                     'strategy_one_hundred_ten_contract\n'
                                                     '        from '
                                                     'src.backend.backtest_fixed_structural_lot_certification_v29 '
                                                     'import certify_fixed_structural_lot_source as '
                                                     'certify_saved_native_source\n'
                                                     '        saved_native=saved_native_release()\n'
                                                     '        '
                                                     'register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n'
                                                     '            '
                                                     'strategy_id=saved_native.executor_strategy_id,revision=saved_native.executor_revision,\n'
                                                     '            '
                                                     'evaluation_interval=saved_native.evaluation_interval,strategy_factory=_strategy_two_factory,\n'
                                                     '            '
                                                     'contract_factory=strategy_one_hundred_ten_contract,\n'
                                                     '            '
                                                     "manifest_authority=NativeManifestAuthority(42,'fixed-structural-lots-from',\n"
                                                     '                '
                                                     'derive_strategy_one_hundred_ten_configuration,verify_prepared_strategy_one_hundred_ten_configuration,\n'
                                                     '                '
                                                     'certify_saved_native_source,management_parent_release)))\n'
                                                     '        register_numbered_strategy(saved_native)\n',
                                                     '')]}}
APPROVED_METADATA_ANCHOR = '4389d6ac443d855fde8b54d5a36cfa5d41f107b3c22da56f41380808a1a6659e'
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
