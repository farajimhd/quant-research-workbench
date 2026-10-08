"""Exact complete parent restoration for declared early-risk capability support."""
import ast
from hashlib import sha256

REVIEWED_PARENT_DELTAS = {'src/trading_runtime/numbered_fixed_strategy.py': ('1a9e4fb20ab7733daf290c0771e5f127b43d483d59c7f4b441a63d284d5aa1b2', '564996201c6846b9b62eb4522f38a9e00fc949ccb1cd3e9453ea2472fd5e555a', [("        optional = {'half_risk_liquidity_policy', 'entry_spread_risk_policy', 'early_original_risk_failure_policy',\n", "        optional = {'half_risk_liquidity_policy', 'entry_spread_risk_policy',\n"), ('        self.early_original_risk_policy\n', ''), ('\n    @property\n    def _declared_early_original_risk_policy(self):\n        import json\n        from .declared_early_original_risk_policy import parse_declared_early_original_risk_policy\n        return parse_declared_early_original_risk_policy(self.release, json.loads(self.policy_json))\n', ''), ('    early_original_risk_policy = _declared_early_original_risk_policy\n', '')])}

def restore_reviewed_parent_source(source, relative):
    relative = relative if relative.startswith('src/') else 'src/' + relative
    recipe = REVIEWED_PARENT_DELTAS.get(relative)
    if recipe is None:
        return source
    current, baseline, edits = recipe
    if sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() != current:
        return source
    restored = source.replace('\r\n', '\n')
    for new, old in edits:
        if restored.count(new) != 1:
            raise ValueError('Declared early-risk parent delta differs: ' + relative)
        restored = restored.replace(new, old, 1)
    if sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != baseline:
        raise ValueError('Complete early-risk parent AST restoration failed: ' + relative)
    return restored
