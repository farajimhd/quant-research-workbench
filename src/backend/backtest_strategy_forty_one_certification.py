"""Reviewed Strategy41 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY41_SOURCE_AST = {'src/trading_runtime/strategy_forty_one_release.py': '8cfd72152770987792482af4328c8516b1b05541ac521003d96993169bd57848', 'pipelines/strategy_one/strategy_forty_one_configuration.py': '9db0d8f7898a3c821dce8753a004182daa2c633f023ea6f002d4d853681fe761', 'src/trading_runtime/strategy_registry.py': '0e51471eb4a24525235276282bba373ed3bd9c511be4132688e81f10079a1a7c', 'src/trading_runtime/numbered_fixed_strategy.py': 'b37f53e1468912b2b70846bc93f24a86d9733b909562d6b5e9686545574254f8', 'src/backend/backtest_strategy_one_configuration.py': '43211a2e24e8a6536fd3ac9bbbe38ca221f92a58d3e0a4ada3272ba34646ca90', 'pipelines/strategy_one/configuration_publisher.py': 'c8b0f5671867a5d117328ba275c2b0b6690d72d1c5bd2c7b433efab3a2ac1f23', 'scripts/clickhouse/publish_strategy_forty_one_configuration.py': '1c9d39b724ebeb0a0a097d7d69f9af5203fdbf8581d26815b851c70528cd78f2', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


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
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy41 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy41 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
