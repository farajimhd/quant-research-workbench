"""Exact reviewed source-only deltas to Strategy108; no execution authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

REVIEWED_EDITS = {'backend/backtest_fixed_lot_initial_recovery_reuse.py': {'current_ast': '97a2d34205e608877070108a9140bb6b934f4dbf44332ec8560c1e7f570984f6',
                                                          'parent_ast': 'da302f46c61d8c553d665de2077e07eccf5aea521326839116e8547f0bdf335c',
                                                          'edits': [('    from '
                                                                     '.backtest_fixed_lot_first_inventory_source_reuse '
                                                                     'import code_snapshot\n'
                                                                     '    '
                                                                     'first_codes,first_bindings=code_snapshot()\n'
                                                                     '    funcs += [fn for fn,_ in '
                                                                     'first_codes]\n',
                                                                     ''),
                                                                    ('        '
                                                                     '(MappingProxyType,Decimal,Enum,date,datetime,OrderedDict,_OPERATION,_READ,_ISSUED,policy.InitialHeldRecoveryReusePolicy,first_bindings))\n',
                                                                     '        '
                                                                     '(MappingProxyType,Decimal,Enum,date,datetime,OrderedDict,_OPERATION,_READ,_ISSUED,policy.InitialHeldRecoveryReusePolicy))\n'),
                                                                    ('    from '
                                                                     '.backtest_fixed_lot_first_inventory_source_reuse '
                                                                     'import load_first_inventory\n'
                                                                     '    '
                                                                     'result,_=load_first_inventory(operation,loader,inventory_key=key)\n'
                                                                     '    '
                                                                     'operation.require();content=_image(result);value=_copy(result)\n',
                                                                     '    '
                                                                     'result=_cold_loader(loader);operation.require();content=_image(result);value=_copy(result)\n')]},
 'backend/backtest_fixed_structural_lot_configuration.py': {'current_ast': 'f44dfe04ca4b579b4175a6ce47b8d2d07c171acabb2b4fcb31ad4aa903bd7d0d',
                                                            'parent_ast': 'b3c316415010ccb0bca3e5d6213ee3ed8ceec9b21d975dd9a51639c981a02da5',
                                                            'edits': [('    from '
                                                                       'src.trading_runtime.first_inventory_source_reuse_policy '
                                                                       'import PARAMETER as '
                                                                       'FIRST_SOURCE_PARAMETER,parse_declared_first_inventory_source_reuse\n'
                                                                       '    '
                                                                       'first_source=parse_declared_first_inventory_source_reuse(contract.release,params.get(FIRST_SOURCE_PARAMETER) '
                                                                       'if type(params) is dict else None)\n'
                                                                       '    if first_source is not '
                                                                       'None:expected.add(FIRST_SOURCE_PARAMETER)\n'
                                                                       '    if '
                                                                       "first_source!=getattr(contract,'first_inventory_source_reuse_policy',None):\n"
                                                                       '        raise '
                                                                       "ValueError('First-inventory source "
                                                                       'reuse differs from exact registered '
                                                                       "factory')\n",
                                                                       '')]},
 'trading_runtime/fixed_structural_lot_selected_exit_contract.py': {'current_ast': '252114f3e8f9776247cd90571e0618cafc358c70b6c5e906bff344452da30e11',
                                                                    'parent_ast': '6128584115f65bbec77fcfbbc635eab78b476e3ba44a9c98b43f70b5382af120',
                                                                    'edits': [('from '
                                                                               '.first_inventory_source_reuse_policy '
                                                                               'import '
                                                                               'FirstInventorySourceReusePolicy\n',
                                                                               ''),
                                                                              ('    '
                                                                               'first_inventory_source_reuse_policy: '
                                                                               'FirstInventorySourceReusePolicy '
                                                                               '| None = None\n',
                                                                               ''),
                                                                              ('        from '
                                                                               '.first_inventory_source_reuse_policy '
                                                                               'import '
                                                                               'require_declared_first_inventory_source_reuse\n'
                                                                               '        '
                                                                               'require_declared_first_inventory_source_reuse(self.release,self.first_inventory_source_reuse_policy)\n',
                                                                               '')]},
 'trading_runtime/strategy_registry.py': {'current_ast': 'f5f34c7df072f50d664db8a22c62bea5b8fce7db1689eea42536248ef99ca65e',
                                          'parent_ast': '1cfb0e1998c02703e37d442adc73657eafc6b1c2544dc06802a511abf411847c',
                                          'edits': [('    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, '
                                                     '13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, '
                                                     '26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, '
                                                     '39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                     '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, 68, 69, 70, '
                                                     '71, 72, 73, 74, 77, 80, 81, 82, 83, 84, 85, 86, 87, '
                                                     '88, 89, 90, 92, 93, 94, 95, 97, 98, 99, 101, 109):\n',
                                                     '    if number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, '
                                                     '13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, '
                                                     '26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, '
                                                     '39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, '
                                                     '55, 56, 57, 58, 59, 60, 61, 64, 65, 66, 68, 69, 70, '
                                                     '71, 72, 73, 74, 77, 80, 81, 82, 83, 84, 85, 86, 87, '
                                                     '88, 89, 90, 92, 93, 94, 95, 97, 98, 99, 101):\n'),
                                                    ('        from .strategy_one_hundred_nine_release import '
                                                     '(release_contract as first_source_release,\n'
                                                     '            '
                                                     'derive_strategy_one_hundred_nine_configuration,verify_prepared_strategy_one_hundred_nine_configuration)\n'
                                                     '        from .strategy_one_hundred_nine_contract '
                                                     'import strategy_one_hundred_nine_contract\n'
                                                     '        from '
                                                     'src.backend.backtest_fixed_structural_lot_certification_v28 '
                                                     'import certify_fixed_structural_lot_source as '
                                                     'certify_first_source\n'
                                                     '        first_source=first_source_release()\n'
                                                     '        '
                                                     'register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n'
                                                     '            '
                                                     'strategy_id=first_source.executor_strategy_id,revision=first_source.executor_revision,\n'
                                                     '            '
                                                     'evaluation_interval=first_source.evaluation_interval,strategy_factory=_strategy_two_factory,\n'
                                                     '            '
                                                     'contract_factory=strategy_one_hundred_nine_contract,\n'
                                                     '            '
                                                     "manifest_authority=NativeManifestAuthority(42,'fixed-structural-lots-from',\n"
                                                     '                '
                                                     'derive_strategy_one_hundred_nine_configuration,verify_prepared_strategy_one_hundred_nine_configuration,\n'
                                                     '                '
                                                     'certify_first_source,management_parent_release)))\n'
                                                     '        register_numbered_strategy(first_source)\n',
                                                     '')]},
 'backend/backtest_fixed_v4_certification.py': {'current_ast': 'c6666dd1e8901454271db97ac3b430ddcc4fd7ee9c3e548d90f15055522d629b',
                                                'parent_ast': '4bf73f71ce4273f56f601e11569d479b9aa99faf3c9312bbc51cd22573d174c2',
                                                'edits': [('def '
                                                           '_reviewed_fixed_lot_configuration_projection(source: '
                                                           'str, name: str, expected: str) -> bool:\n'
                                                           '    """Pin this exact registration delta and '
                                                           'prove whole legacy module restoration."""\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v28 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v28\n'
                                                           '    source = restore_v28(source, '
                                                           "'backend/backtest_strategy_one_configuration.py')\n",
                                                           'def '
                                                           '_reviewed_fixed_lot_configuration_projection(source: '
                                                           'str, name: str, expected: str) -> bool:\n'
                                                           '    """Pin this exact registration delta and '
                                                           'prove whole legacy module restoration."""\n'),
                                                          ('def '
                                                           '_reviewed_fixed_lot_management_projection(source: '
                                                           'str, name: str, expected: str) -> bool:\n'
                                                           '    """Prove this exact selected owner leaves '
                                                           'the complete default manager unchanged."""\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v28 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v28\n'
                                                           '    source = restore_v28(source, '
                                                           "'src/backend/backtest_strategy_one_management.py')\n",
                                                           'def '
                                                           '_reviewed_fixed_lot_management_projection(source: '
                                                           'str, name: str, expected: str) -> bool:\n'
                                                           '    """Prove this exact selected owner leaves '
                                                           'the complete default manager unchanged."""\n'),
                                                          ('    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'
                                                           '    supplied_source = source\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v28 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v28\n'
                                                           '    source = restore_v28(source, relative)\n',
                                                           '    """Apply only exact reviewed AST edits; '
                                                           'complete retained legacy pin remains '
                                                           'required."""\n'
                                                           '    supplied_source = source\n'),
                                                          ('    supplied_source = source\n'
                                                           '    relative = relative.removeprefix("src/")\n'
                                                           '    from '
                                                           '.backtest_fixed_structural_lot_compatibility_v28 '
                                                           'import restore_reviewed_parent_source as '
                                                           'restore_v28\n'
                                                           '    source = restore_v28(source, relative)\n',
                                                           '    supplied_source = source\n'
                                                           '    relative = '
                                                           'relative.removeprefix("src/")\n')]}}
APPROVED_METADATA_ANCHOR = 'e7a684335a16d72f54ee1207e7c5f79475edd3fe1f2f79e29093f73d725e961e'
APPROVED_SELF_AST = '67f7ecc61e4a10adcd187257ed62b1e0a1dd72aaf060afaf42afa53ef7fb1b3c'

def restore_reviewed_parent_source(source, relative):
    own = Path(__file__)
    fresh_source = own.read_text(encoding='utf-8')
    tree = ast.parse(fresh_source)
    names = ('REVIEWED_EDITS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST')
    fresh = {}
    for name in names:
        declarations = [n for n in tree.body if type(n) is ast.Assign and len(n.targets) == 1 and (type(n.targets[0]) is ast.Name) and (n.targets[0].id == name)]
        if len(declarations) != 1:
            raise ValueError('First-inventory source compatibility declaration shape differs')
        fresh[name] = ast.literal_eval(declarations[0].value)
    if fresh != dict(zip(names, (REVIEWED_EDITS, APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST))):
        raise ValueError('First-inventory source compatibility loaded and fresh metadata differs')
    for node in tree.body:
        if type(node) is ast.Assign and len(node.targets) == 1 and (type(node.targets[0]) is ast.Name):
            if node.targets[0].id in names:
                node.value = ast.Constant(None)
    if sha256(ast.unparse(tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('First-inventory source compatibility envelope differs')
    if sha256(json.dumps(REVIEWED_EDITS, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != APPROVED_METADATA_ANCHOR:
        raise ValueError('First-inventory source compatibility metadata anchor differs')
    recipe = REVIEWED_EDITS.get(relative.removeprefix('src/'))
    if recipe is None:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('First-inventory source compatibility changed during restoration')
        return source
    if sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() != recipe['current_ast']:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('First-inventory source compatibility changed during restoration')
        return source
    restored = source
    for current, previous in recipe['edits']:
        if not current or restored.count(current) != 1:
            raise ValueError('First-inventory source compatibility exact reviewed edit differs: ' + relative)
        restored = restored.replace(current, previous, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != recipe['parent_ast']:
        raise ValueError('First-inventory source compatibility retained parent AST differs: ' + relative)
    if own.read_text(encoding='utf-8') != fresh_source:
        raise ValueError('First-inventory source compatibility changed during restoration')
    return restored
