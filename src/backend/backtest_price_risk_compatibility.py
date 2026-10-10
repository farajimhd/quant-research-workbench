"""Exact whole-module projection of the declared price-risk extension.

This restores the reviewed parent source; it grants no runtime admission.
Unknown edits in either retained code or the extension fail closed.
"""
import ast
from hashlib import sha256

PARENT_CODE_COMMIT = '45bca1109a4f93742e26fa78dfe7a4492cdab37a'
PARENT_AST_HASHES = {
    'src/trading_runtime/numbered_fixed_strategy.py': 'a94f7b006a96f706b1f6770e4ea396cd2fd0bc21f11d69c1ba9ab816f09e11c6',
    'src/trading_runtime/strategy_followthrough_exit.py': 'e5ed9aa87f606bed29e550756d11e7fa1445d9ffb47f69d4f1402bfdb17b9984',
    'src/backend/backtest_strategy_one_management.py': 'b13949a2eca3388b2806bde92cff6fc1c79021fd2e251b165e102ea48888a343',
}

_PROPERTY = '''
@property
def price_confirmed_original_risk_policy(self):
    import json
    from .price_confirmed_original_risk import parse_price_confirmed_original_risk_policy
    return parse_price_confirmed_original_risk_policy(self.release, json.loads(self.policy_json))
'''
_CONSTRUCTOR = '''
price_policy = self.price_confirmed_original_risk_policy
if price_policy is not None and (not self.allows_followthrough_failure_exit
        or self.confirmed_original_risk_policy is not None
        or self.premarket_confirmed_original_risk_policy is not None):
    raise ValueError('Price risk extension requires ordinary completed failure authority')
'''
_VALIDATOR = '''
if actual != witness:
    price_policy = getattr(numbered_fixed_strategy(strategy_number),
                           'price_confirmed_original_risk_policy', None)
    if price_policy is not None:
        from .price_confirmed_original_risk import price_confirmed_original_risk_failure
        actual = price_confirmed_original_risk_failure(value, policy=price_policy)
'''
_MANAGER = '''
if boundary_ms % 5000 == 0:
    price_policy = getattr(self.contract, 'price_confirmed_original_risk_policy', None)
    if price_policy is not None:
        from src.trading_runtime.price_confirmed_original_risk import price_confirmed_original_risk_failure
        witness = price_confirmed_original_risk_failure(completed, policy=price_policy)
        if witness is not None:
            entry = self.runtime._strategy_one_entry_intent(source)
            await self.runtime.submit_followthrough_failure(financial, witness, entry.intent_id)
            self._profit_arm_financials.pop(key, None)
            return
'''


def _strip(body, fragment):
    expected = ast.parse(fragment).body
    dumps = lambda nodes: tuple(ast.dump(node, include_attributes=False) for node in nodes)
    target = dumps(expected)
    matches = [i for i in range(len(body)-len(expected)+1)
               if dumps(body[i:i+len(expected)]) == target]
    if len(matches) != 1:
        raise ValueError('Price-risk delta differs from exact reviewed source')
    i = matches[0]
    del body[i:i+len(expected)]


def restore_price_risk_parent_source(source, relative):
    if type(source) is not str or type(relative) is not str or relative not in PARENT_AST_HASHES:
        raise ValueError('Known exact price-risk source module required')
    tree = ast.parse(source)
    if relative.endswith('numbered_fixed_strategy.py'):
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'DeclaredFixedStrategyContract']
        if len(classes) != 1:
            raise ValueError('Declared fixed class cardinality changed')
        owner = classes[0]
        _strip(owner.body, _PROPERTY)
        functions = [node for node in owner.body if isinstance(node, ast.FunctionDef)
                     and node.name == '__post_init__']
        if len(functions) != 1:
            raise ValueError('Declared constructor cardinality changed')
        body = functions[0].body
        _strip(body, _CONSTRUCTOR)
        options = [node.value for node in body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id == 'optional' and isinstance(node.value, ast.Set)]
        if len(options) != 1:
            raise ValueError('Declared optional-policy cardinality changed')
        additions = [node for node in options[0].elts if isinstance(node, ast.Constant)
                     and type(node.value) is str and node.value == 'price_confirmed_original_risk_policy']
        if len(additions) != 1:
            raise ValueError('Declared price-policy option changed')
        options[0].elts.remove(additions[0])
    else:
        name = 'validate_witness' if relative.endswith('strategy_followthrough_exit.py') else 'on_management'
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
        if len(functions) != 1:
            raise ValueError('Price-risk consumer cardinality changed')
        _strip(functions[0].body, _VALIDATOR if name == 'validate_witness' else _MANAGER)
    restored = ast.unparse(tree)
    if sha256(restored.encode()).hexdigest() != PARENT_AST_HASHES[relative]:
        raise ValueError('Price-risk projection changed retained parent source')
    return restored + '\n'
