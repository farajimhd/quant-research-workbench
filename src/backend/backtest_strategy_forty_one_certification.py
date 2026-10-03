"""Reviewed Strategy41 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY41_SOURCE_AST = {'src/trading_runtime/strategy_forty_one_release.py': '8cfd72152770987792482af4328c8516b1b05541ac521003d96993169bd57848', 'pipelines/strategy_one/strategy_forty_one_configuration.py': '9db0d8f7898a3c821dce8753a004182daa2c633f023ea6f002d4d853681fe761', 'src/trading_runtime/strategy_registry.py': 'd6b06838efb9d86db9e8ccf258c0d434913a28a583cae0dd7bfa9dfbd4bfe842', 'src/trading_runtime/numbered_fixed_strategy.py': '4572b8612247111a011c396f39ef14d879dca99b6988a81b0cc5a60f9103e861', 'src/backend/backtest_strategy_one_configuration.py': 'd75eed6ac5a3404930474e524bff5f905ee4dc42cae83c6d27444a50f56b45df', 'pipelines/strategy_one/configuration_publisher.py': '571e90ade71025ce0fb71cea99895c0e20145b12dbefc1b2398efbd7a2601cbc', 'scripts/clickhouse/publish_strategy_forty_one_configuration.py': '1c9d39b724ebeb0a0a097d7d69f9af5203fdbf8581d26815b851c70528cd78f2'}


def certify_strategy_forty_one_source(*, source_overrides=None):
    """Pin the additional policy and transport; inherited routes are certified separately."""
    overrides = source_overrides or {}
    if set(overrides) - set(STRATEGY41_SOURCE_AST):
        raise ValueError('Strategy41 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY41_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            from .backtest_historical_strategy_projection import historical_strategy_tree
            tree = historical_strategy_tree(ast.parse(source), relative)
        except SyntaxError as exc:
            raise ValueError('Strategy41 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy41 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
