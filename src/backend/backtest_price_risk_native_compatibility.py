"""Exact source restoration for native historical-parent metadata and registration."""
import ast
from hashlib import sha256
from pathlib import Path

RESTORATIONS = {'src/trading_runtime/declared_native_manifest.py': {'parent_ast': 'c47104ec813acd9f299b4152176ba65d339892c4dcb5c1b6a2f4a9be4838c39b', 'current_ast': 'f7569d6c368baa6ae46da8666f558b2c0be9cf879922bf644202686f30b8d5b4', 'edits': (("            raise ValueError('Native manifest parent differs from exact release factory')\n        if self.historical_parent_proof is not None:\n            if type(self.historical_parent_proof) is not HistoricalParentSourceProof:\n                raise ValueError('Native manifest historical parent proof has a foreign type')\n            self.historical_parent_proof.verify(parent)\n\n", "            raise ValueError('Native manifest parent differs from exact release factory')\n\n"), ('    parent_release_factory: Callable\n    historical_parent_proof: HistoricalParentSourceProof | None = None\n\n', '    parent_release_factory: Callable\n\n'), ('from typing import Callable\nfrom .historical_parent_source_proof import HistoricalParentSourceProof\n\n', 'from typing import Callable\n\n'))}, 'src/backend/backtest_fixed_v4_certification.py': {'parent_ast': '7913e74fae450e4b937acebf6eea9bb5455de6f72e24dcc22946c67d12636e77', 'current_ast': 'c7b31a2b878a74aac1a5b3bc0c72f8c0fc49342382fc5ff7d3c36cdb685c81ff', 'edits': (("                and declared_fixed_structural_lot_contract(strategy_number) is None):\n            from src.trading_runtime.numbered_fixed_strategy import DeclaredFixedStrategyContract, numbered_fixed_strategy\n            selected = numbered_fixed_strategy(strategy_number)\n            if (type(selected) is not DeclaredFixedStrategyContract\n                    or selected.price_confirmed_original_risk_policy is None):\n                raise ValueError('Native manifest lacks exact supported typed semantics')\n        parent_proof = (authority.historical_parent_proof.verify(authority.parent_release_factory())\n                        if authority.historical_parent_proof is not None\n                        else certify_numbered_fixed_v4_projection(authority.parent_number))\n        additional_proof = authority.certify_source()\n", "                and declared_fixed_structural_lot_contract(strategy_number) is None):\n            raise ValueError('Native manifest lacks exact supported typed semantics')\n        parent_proof = certify_numbered_fixed_v4_projection(authority.parent_number)\n        additional_proof = authority.certify_source()\n"),)}, 'src/trading_runtime/strategy_registry.py': {'parent_ast': '6c5320265f9f677233aa5b32a3c119ac74232087d9a4039910c0c82dd7dd1b31', 'current_ast': 'c10e9974c9216619c9c21bee7c2aa4dee9fdbde220ea72134cd4cf04d9ce64ca', 'edits': (('        register_numbered_strategy(context_capacity)\n        from .strategy_one_hundred_thirteen_release import release_contract as price_risk_release\n        from .strategy_one_hundred_thirteen_contract import strategy_one_hundred_thirteen_contract\n        from .strategy_one_hundred_thirteen_configuration import (\n            SOURCE_PREFIX, derive_strategy_one_hundred_thirteen_configuration,\n            verify_strategy_one_hundred_thirteen_manifest, historical_parent_source_proof)\n        from .strategy_fifty_seven_release import release_contract as price_risk_parent_release\n        from src.backend.backtest_price_risk_certification import certify_price_confirmed_original_risk_source\n        price_risk = price_risk_release()\n        register_fixed_strategy_executor(FixedStrategyExecutorRegistration(\n            strategy_id=price_risk.executor_strategy_id, revision=price_risk.executor_revision,\n            evaluation_interval=price_risk.evaluation_interval, strategy_factory=_strategy_two_factory,\n            contract_factory=strategy_one_hundred_thirteen_contract,\n            manifest_authority=NativeManifestAuthority(57, SOURCE_PREFIX,\n                derive_strategy_one_hundred_thirteen_configuration,\n                verify_strategy_one_hundred_thirteen_manifest,\n                certify_price_confirmed_original_risk_source, price_risk_parent_release,\n                historical_parent_proof=historical_parent_source_proof())))\n        register_numbered_strategy(price_risk)\n        _NUMBERED_FIXED_REGISTERED = True\n', '        register_numbered_strategy(context_capacity)\n        _NUMBERED_FIXED_REGISTERED = True\n'),)}}
APPROVED_SELF_AST = 'f594927fe590bec7b10d2a473cf47b0a28924ac69bd075cf006907b7552b2660'


def restore_native_price_risk_parent_source(source, relative):
    own = Path(__file__)
    fresh_source = own.read_text(encoding='utf-8')
    tree = ast.parse(fresh_source)
    loaded = {'RESTORATIONS': RESTORATIONS, 'APPROVED_SELF_AST': APPROVED_SELF_AST}
    fresh = {}
    for name in loaded:
        nodes = [n for n in tree.body if type(n) is ast.Assign and len(n.targets) == 1
                 and type(n.targets[0]) is ast.Name and n.targets[0].id == name]
        if len(nodes) != 1:
            raise ValueError('Native price-risk restoration declaration differs')
        fresh[name] = ast.literal_eval(nodes[0].value)
        nodes[0].value = ast.Constant(None)
    if fresh != loaded or sha256(ast.unparse(tree).encode()).hexdigest() != APPROVED_SELF_AST:
        raise ValueError('Native price-risk restoration envelope differs')
    recipe = RESTORATIONS.get(relative)
    if recipe is None:
        return source
    if sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() != recipe['current_ast']:
        raise ValueError('Native price-risk current source differs: ' + relative)
    restored = source
    for current, previous in recipe['edits']:
        if not current or restored.count(current) != 1:
            raise ValueError('Native price-risk exact source delta differs: ' + relative)
        restored = restored.replace(current, previous, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != recipe['parent_ast']:
        raise ValueError('Native price-risk retained parent differs: ' + relative)
    if own.read_text(encoding='utf-8') != fresh_source:
        raise ValueError('Native price-risk restoration changed during verification')
    return restored
