"""Exact reviewed management-reuse delta and retained waiting-source composition."""
import ast
from hashlib import sha256
import json
from pathlib import Path
REVIEWED_EDITS = {'backend/replay_run_service.py': {'current_ast': 'f44179554a5200274a71f4b247d8deba2a494a8e6d52f4b39fca0c49eb1df406', 'parent_ast': '140ed6acfe829e689332b736fd26a4e6f67b13b34990fb5642c87e938bf419c6', 'edits': [('        from .backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract\n        selected_lot_session = None\n        if declared_fixed_structural_lot_contract(strategy_number) is not None:\n            from .backtest_fixed_structural_lot_execution_v20 import prepare_fixed_structural_lot_session\n            from .backtest_market_data import readonly_clickhouse_client\n            selected_lot_session = await asyncio.to_thread(prepare_fixed_structural_lot_session, plans=plans,\n                number=strategy_number, run_id=self.run_id, session_date=self.definition.session_date,\n', '        from .backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract\n        selected_lot_session = None\n        if declared_fixed_structural_lot_contract(strategy_number) is not None:\n            from .backtest_fixed_structural_lot_execution_v19 import prepare_fixed_structural_lot_session\n            from .backtest_market_data import readonly_clickhouse_client\n            selected_lot_session = await asyncio.to_thread(prepare_fixed_structural_lot_session, plans=plans,\n                number=strategy_number, run_id=self.run_id, session_date=self.definition.session_date,\n'), ('        from .backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract\n        selected_lot_session = None\n        if declared_fixed_structural_lot_contract(strategy_number) is not None:\n            from .backtest_fixed_structural_lot_execution_v20 import prepare_fixed_structural_lot_session\n            from .backtest_market_data import readonly_clickhouse_client\n            selected_lot_session = await asyncio.to_thread(prepare_fixed_structural_lot_session, plans=plans,\n                number=strategy_number,run_id=run_id,session_date=definition.session_date,\n', '        from .backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract\n        selected_lot_session = None\n        if declared_fixed_structural_lot_contract(strategy_number) is not None:\n            from .backtest_fixed_structural_lot_execution_v19 import prepare_fixed_structural_lot_session\n            from .backtest_market_data import readonly_clickhouse_client\n            selected_lot_session = await asyncio.to_thread(prepare_fixed_structural_lot_session, plans=plans,\n                number=strategy_number,run_id=run_id,session_date=definition.session_date,\n')]}, 'backend/backtest_fixed_v4_certification.py': {'current_ast': '01a1c9a691dc4559524ea43c8f56855c496ed5cf781d6c91358f738a7c56406d', 'parent_ast': '236929012ccd40ac64766ce83b7a8515d2acb76aebf99cead120518c9edb1954', 'edits': [("    from .backtest_fixed_structural_lot_compatibility_v21 import restore_reviewed_parent_source as restore_v21\n    source = restore_v21(source, 'backend/backtest_strategy_one_configuration.py')\n", ''), ('    supplied_source = source\n    from .backtest_fixed_structural_lot_compatibility_v21 import restore_reviewed_parent_source as restore_v21\n    source = restore_v21(source, relative)\n    from .backtest_fixed_structural_lot_compatibility_v20 import restore_reviewed_parent_source as restore_v20\n', '    supplied_source = source\n    from .backtest_fixed_structural_lot_compatibility_v20 import restore_reviewed_parent_source as restore_v20\n'), ('    relative = relative.removeprefix("src/")\n    from .backtest_fixed_structural_lot_compatibility_v21 import restore_reviewed_parent_source as restore_v21\n    source = restore_v21(source, relative)\n    from .backtest_fixed_structural_lot_compatibility_v20 import restore_reviewed_parent_source as restore_v20\n', '    relative = relative.removeprefix("src/")\n    from .backtest_fixed_structural_lot_compatibility_v20 import restore_reviewed_parent_source as restore_v20\n')]}, 'trading_runtime/strategy_registry.py': {'current_ast': '5a8ad751a601a32a84f0203ffe9c62e7b2c86c915c93895fd2d1704659d84a85', 'parent_ast': '3f06f5c84e1ab9b8e84c80d71f2eced6a4fd605e2f9189264768f2484d5ac0e6', 'edits': [("        from .strategy_one_hundred_two_release import (release_contract as preparation_release,\n            derive_strategy_one_hundred_two_configuration,\n            verify_prepared_strategy_one_hundred_two_configuration)\n        from .strategy_one_hundred_two_contract import strategy_one_hundred_two_contract\n        from src.backend.backtest_fixed_structural_lot_certification_v21 import certify_fixed_structural_lot_source as certify_preparation_source\n        preparation = preparation_release()\n        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n            strategy_id=preparation.executor_strategy_id, revision=preparation.executor_revision,\n            evaluation_interval=preparation.evaluation_interval, strategy_factory=_strategy_two_factory,\n            contract_factory=strategy_one_hundred_two_contract,\n            manifest_authority=NativeManifestAuthority(42, 'fixed-structural-lots-from',\n                derive_strategy_one_hundred_two_configuration,\n                verify_prepared_strategy_one_hundred_two_configuration,\n                certify_preparation_source, management_parent_release)))\n        register_numbered_strategy(preparation)\n", '')]}}
APPROVED_METADATA_ANCHOR = '63aa121ba55b33e2af38b918edb17de5f41f09f78c5cdc09923be9eec0072982'
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
