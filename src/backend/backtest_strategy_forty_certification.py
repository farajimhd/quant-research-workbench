"""Reviewed Strategy40 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY40_SOURCE_AST = {'src/trading_runtime/strategy_forty_release.py': '9c9ca6c91608c67c53466f5f806b2382961be0273d33d1b839e8cf26105b356e', 'pipelines/strategy_one/strategy_forty_configuration.py': 'c104e52584cf74c473c724e94a5bd9aa84e1d8a9900a144c008b13911212f0cb', 'src/trading_runtime/strategy_registry.py': '0e51471eb4a24525235276282bba373ed3bd9c511be4132688e81f10079a1a7c', 'src/trading_runtime/numbered_fixed_strategy.py': 'b37f53e1468912b2b70846bc93f24a86d9733b909562d6b5e9686545574254f8', 'src/backend/backtest_strategy_one_configuration.py': '43211a2e24e8a6536fd3ac9bbbe38ca221f92a58d3e0a4ada3272ba34646ca90', 'pipelines/strategy_one/configuration_publisher.py': 'c8b0f5671867a5d117328ba275c2b0b6690d72d1c5bd2c7b433efab3a2ac1f23', 'scripts/clickhouse/publish_strategy_forty_configuration.py': 'f14db18ef05f42c90ff7d4dbc58b366084efd2ad9dcafc66009958b81aa8ff0b', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


def certify_strategy_forty_source(*, source_overrides=None):
    """Pin the additional policy and transport; inherited routes are certified separately."""
    overrides = source_overrides or {}
    if set(overrides) - set(STRATEGY40_SOURCE_AST):
        raise ValueError('Strategy40 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY40_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy40 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy40 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
