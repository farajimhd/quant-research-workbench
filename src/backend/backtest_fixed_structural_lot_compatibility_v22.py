"""Exact reviewed management-reuse delta and retained waiting-source composition."""
import ast
from hashlib import sha256
import json
from pathlib import Path
REVIEWED_EDITS = {'backend/backtest_fixed_lot_management_reuse.py': {'current_ast': '25477de0fe081c4d9c6f2d2502a75edf58db33a18d2be7578f80f3b2b6e9e065', 'parent_ast': 'd5f347c63f572e889a85e3ab35bbbbd424580f39293f817bc5a2d7c9acddf88a', 'edits': [("    # Original authority can replay historical predecessors. Its nested reads\n    # must execute their complete verifiers, rather than inherit the current\n    # decision's prefix-bound cache or recursively reuse its authority.\n    suspended = _ACTIVE.set(None)\n", ''), ('    finally:\n        _ACTIVE.reset(suspended)\n        _CONTEXT_OWNER.reset(token)\n', '    finally:\n        _CONTEXT_OWNER.reset(token)\n')]}, 'backend/backtest_fixed_v4_certification.py': {'current_ast': '211133dc0d29c85325609cb0c2921646622b4558c3bcedf293e4d191e11751ce', 'parent_ast': '01a1c9a691dc4559524ea43c8f56855c496ed5cf781d6c91358f738a7c56406d', 'edits': [("    from .backtest_fixed_structural_lot_compatibility_v22 import restore_reviewed_parent_source as restore_v22\n    source = restore_v22(source, 'backend/backtest_strategy_one_configuration.py')\n", ''), ('    supplied_source = source\n    from .backtest_fixed_structural_lot_compatibility_v22 import restore_reviewed_parent_source as restore_v22\n    source = restore_v22(source, relative)\n    from .backtest_fixed_structural_lot_compatibility_v21 import restore_reviewed_parent_source as restore_v21\n', '    supplied_source = source\n    from .backtest_fixed_structural_lot_compatibility_v21 import restore_reviewed_parent_source as restore_v21\n'), ('    relative = relative.removeprefix("src/")\n    from .backtest_fixed_structural_lot_compatibility_v22 import restore_reviewed_parent_source as restore_v22\n    source = restore_v22(source, relative)\n    from .backtest_fixed_structural_lot_compatibility_v21 import restore_reviewed_parent_source as restore_v21\n', '    relative = relative.removeprefix("src/")\n    from .backtest_fixed_structural_lot_compatibility_v21 import restore_reviewed_parent_source as restore_v21\n')]}, 'trading_runtime/strategy_registry.py': {'current_ast': '5ec8e4444b8ae7a5681aa4ef5d85bca5158f3390d937cc64981215fd61f020b3', 'parent_ast': '5a8ad751a601a32a84f0203ffe9c62e7b2c86c915c93895fd2d1704659d84a85', 'edits': [("        from .strategy_one_hundred_three_release import (release_contract as isolation_release,\n            derive_strategy_one_hundred_three_configuration,\n            verify_prepared_strategy_one_hundred_three_configuration)\n        from .strategy_one_hundred_three_contract import strategy_one_hundred_three_contract\n        from src.backend.backtest_fixed_structural_lot_certification_v22 import certify_fixed_structural_lot_source as certify_isolation_source\n        isolation = isolation_release()\n        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n            strategy_id=isolation.executor_strategy_id, revision=isolation.executor_revision,\n            evaluation_interval=isolation.evaluation_interval, strategy_factory=_strategy_two_factory,\n            contract_factory=strategy_one_hundred_three_contract,\n            manifest_authority=NativeManifestAuthority(42, 'fixed-structural-lots-from',\n                derive_strategy_one_hundred_three_configuration,\n                verify_prepared_strategy_one_hundred_three_configuration,\n                certify_isolation_source, management_parent_release)))\n        register_numbered_strategy(isolation)\n", '')]}}
APPROVED_METADATA_ANCHOR = 'd38d93553f2ade6bb1e77747bbb50333a6fff6c521c519e7d4993d5da66d2808'
APPROVED_SELF_AST = '4cb836ee1bbcd8f7068081673632575f480f028d3a7e7f0b92b981c502307fb7'

def restore_reviewed_parent_source(source, relative):
    own = Path(__file__)
    fresh_source = own.read_text(encoding='utf-8')
    tree = ast.parse(fresh_source)
    names = ('REVIEWED_EDITS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST')
    fresh = {}
    for name in names:
        declarations = [n for n in tree.body if type(n) is ast.Assign and len(n.targets) == 1 and (type(n.targets[0]) is ast.Name) and (n.targets[0].id == name)]
        if len(declarations) != 1:
            raise ValueError('Native preparation compatibility declaration shape differs')
        fresh[name] = ast.literal_eval(declarations[0].value)
    if fresh != dict(zip(names, (REVIEWED_EDITS, APPROVED_METADATA_ANCHOR, APPROVED_SELF_AST))):
        raise ValueError('Native preparation compatibility loaded and fresh metadata differs')
    for node in tree.body:
        if type(node) is ast.Assign and len(node.targets) == 1 and (type(node.targets[0]) is ast.Name):
            if node.targets[0].id in names:
                node.value = ast.Constant(None)
    if sha256(ast.unparse(tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Native preparation compatibility envelope differs')
    if sha256(json.dumps(REVIEWED_EDITS, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != APPROVED_METADATA_ANCHOR:
        raise ValueError('Native preparation compatibility metadata anchor differs')
    recipe = REVIEWED_EDITS.get(relative.removeprefix('src/'))
    if recipe is None:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Native preparation compatibility changed during restoration')
        return source
    if sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() != recipe['current_ast']:
        if own.read_text(encoding='utf-8') != fresh_source:
            raise ValueError('Native preparation compatibility changed during restoration')
        return source
    restored = source
    for current, previous in recipe['edits']:
        if not current or restored.count(current) != 1:
            raise ValueError('Native preparation compatibility exact reviewed edit differs: ' + relative)
        restored = restored.replace(current, previous, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != recipe['parent_ast']:
        raise ValueError('Native preparation compatibility retained parent AST differs: ' + relative)
    if own.read_text(encoding='utf-8') != fresh_source:
        raise ValueError('Native preparation compatibility changed during restoration')
    return restored
