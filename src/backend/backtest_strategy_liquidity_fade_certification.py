"""Prepared liquidity-candidate source seal, separate from runtime admission."""
import ast
from hashlib import sha256
import json
from pathlib import Path


LIQUIDITY_FADE_SOURCE_AST = {
    'src/backend/backtest_strategy_liquidity_fade.py': '9dc22d73da02a6a13d9c3176aa4ba9877f2199643c73575272265b0ec08d1b20',
    'src/trading_runtime/strategy_liquidity_fade_failure.py': '25ec4e6e986c8cd684740d9140ac5b659b36f386170d3bad6ce2af2e5569350e',
    'src/trading_runtime/strategy_liquidity_fade_exit.py': '2ae17b94d208e79b07343b159a70f231f940faaf9b9aaf23be8500b3e243aa14',
    'src/trading_runtime/arte_liquidity_fade_failure_v4.py': '213c91478a51fc73e49aa51ffa0fac1858bc09fa899cc0a993fbc31f650e4d73',
    'src/trading_runtime/strategy_liquidity_fade_source.py': 'bf9c1c287bb9d1d043baacb3bedb0f2f82d74c1bd7b35fb8549fa1189cc17882',
    'src/trading_runtime/strategy_liquidity_fade_market_source.py': '45949942e0dfe40d32562e83ef19ef61ecea567a050934b6da5a4f1c3ee50420',
}


def certify_prepared_liquidity_fade_source(*, source_overrides=None):
    """Reject changed prepared rules, rolling arithmetic and authority checks.

    This is a six-module implementation seal. It does not certify inherited
    Strategy 34 routes, install Strategy 35, grant native writer admission,
    attest market inputs, or establish financial backtest performance.
    """
    overrides = source_overrides or {}
    if set(overrides) - set(LIQUIDITY_FADE_SOURCE_AST):
        raise ValueError('Liquidity source override is outside prepared authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in LIQUIDITY_FADE_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Liquidity source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Prepared liquidity source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
