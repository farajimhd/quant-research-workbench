"""Exact reviewed management-reuse delta and retained waiting-source composition."""
import ast
from hashlib import sha256
import json
from pathlib import Path
REVIEWED_EDITS = {'trading_runtime/arte_oms_projection.py': {'current_ast': '4d4355bf403200d1d53558e5070624ecbbd6232b7916a22d81a6679aa1df2dfc', 'parent_ast': '786291db282efced570a31474814102eef2bdb9dc74d8ddffa64cbe5ecb8d07b', 'edits': [('    from .independent_lot_repair_creation_lineage import metadata_at_creation\n    creation_metadata = metadata_at_creation(group, order, metadata, proofs,\n        source=fixed_lot_source, run_id=source_run_id, strategy_id=source_strategy_id,\n        strategy_revision=source_strategy_revision, sequence=source_sequence,\n        boundary=source_boundary)\n    if creation_metadata is not None:\n        return creation_metadata\n', '')]}, 'backend/backtest_fixed_v4_certification.py': {'current_ast': '5da3331e3c70c1fd3f57dad75da2fb92d27fa4453bd26047bfd922250b9abbb6', 'parent_ast': '07e4ada17da1e6ea2e30f464626408b19711cfaf9fffb6e2a4645acdf12cf51c', 'edits': [("    from .backtest_fixed_structural_lot_compatibility_v24 import restore_reviewed_parent_source as restore_v24\n    source = restore_v24(source, 'backend/backtest_strategy_one_configuration.py')\n", ''), ('    supplied_source = source\n    from .backtest_fixed_structural_lot_compatibility_v24 import restore_reviewed_parent_source as restore_v24\n    source = restore_v24(source, relative)\n    from .backtest_fixed_structural_lot_compatibility_v23 import restore_reviewed_parent_source as restore_v23\n', '    supplied_source = source\n    from .backtest_fixed_structural_lot_compatibility_v23 import restore_reviewed_parent_source as restore_v23\n'), ('    relative = relative.removeprefix("src/")\n    from .backtest_fixed_structural_lot_compatibility_v24 import restore_reviewed_parent_source as restore_v24\n    source = restore_v24(source, relative)\n    from .backtest_fixed_structural_lot_compatibility_v23 import restore_reviewed_parent_source as restore_v23\n', '    relative = relative.removeprefix("src/")\n    from .backtest_fixed_structural_lot_compatibility_v23 import restore_reviewed_parent_source as restore_v23\n')]}, 'trading_runtime/strategy_registry.py': {'current_ast': '379110cbf98367ce2c8b338edb2cdb5963e8d6bf59aa6e463f094c4336d6588b', 'parent_ast': 'b580660fa5524baece653dcc059e45433db1416c7073a3095f65f6113ff51f26', 'edits': [("        from .strategy_one_hundred_five_release import (release_contract as repair_lineage_release,\n            derive_strategy_one_hundred_five_configuration,\n            verify_prepared_strategy_one_hundred_five_configuration)\n        from .strategy_one_hundred_five_contract import strategy_one_hundred_five_contract\n        from src.backend.backtest_fixed_structural_lot_certification_v24 import certify_fixed_structural_lot_source as certify_repair_lineage_source\n        repair_lineage = repair_lineage_release()\n        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n            strategy_id=repair_lineage.executor_strategy_id, revision=repair_lineage.executor_revision,\n            evaluation_interval=repair_lineage.evaluation_interval, strategy_factory=_strategy_two_factory,\n            contract_factory=strategy_one_hundred_five_contract,\n            manifest_authority=NativeManifestAuthority(42, 'fixed-structural-lots-from',\n                derive_strategy_one_hundred_five_configuration,\n                verify_prepared_strategy_one_hundred_five_configuration,\n                certify_repair_lineage_source, management_parent_release)))\n        register_numbered_strategy(repair_lineage)\n", '')]}, 'trading_runtime/independent_lot_protection.py': {'current_ast': 'c30145d26b4bb9911eb827c25902b824ba5c2697dd26ce16cdefaa3ed9601518', 'parent_ast': '498b88e02d483d3b8c8c327b874045df3946a80cb2cc02ac472737e1ccdd36af', 'edits': [('                        from .independent_lot_repair_retirement import record_terminal_repair_readback\n                        await record_terminal_repair_readback(manager, group, index, order)\n', '')]}, 'backend/backtest_fixed_structural_lot_native.py': {'current_ast': '4323175fa0934e3ae7db8d8f8b27aebf01f5f72211059cf837a314866ac8c1cc', 'parent_ast': '434e61312731974232e0fdc51eff33917046fe46e08781ee30934ea30d1ca773', 'edits': [('        from src.trading_runtime.independent_lot_repair_retirement import bind_retirement_source\n        bind_retirement_source(runtime.order_manager,self.source)\n', '')]}}
APPROVED_METADATA_ANCHOR = '82cb2acb86487c1d3cf4459c6a892dc2c89de590feb52fd7e99def8baecd2b8b'
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
