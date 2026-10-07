"""Reviewed Strategy39 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY39_SOURCE_AST = {'src/trading_runtime/strategy_thirty_nine_release.py': '0b3a73c0d34d2810e815f07ab21b1d6e5dd4c128b087c1fea77abb662bebf155', 'pipelines/strategy_one/strategy_thirty_nine_configuration.py': 'ab868da6d6f1ed4be17564539f894bdb535d64804cc4931fb8c8d07017ae315f', 'src/trading_runtime/strategy_registry.py': '0e51471eb4a24525235276282bba373ed3bd9c511be4132688e81f10079a1a7c', 'src/trading_runtime/numbered_fixed_strategy.py': 'b37f53e1468912b2b70846bc93f24a86d9733b909562d6b5e9686545574254f8', 'src/backend/backtest_strategy_one_configuration.py': '43211a2e24e8a6536fd3ac9bbbe38ca221f92a58d3e0a4ada3272ba34646ca90', 'pipelines/strategy_one/configuration_publisher.py': 'c8b0f5671867a5d117328ba275c2b0b6690d72d1c5bd2c7b433efab3a2ac1f23', 'scripts/clickhouse/publish_strategy_thirty_nine_configuration.py': '65985cb49ccd17778f47c970cb0413d2e88b2b7d96c94d67b6640057f9fb97f3', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


def certify_strategy_thirty_nine_source(*, source_overrides=None):
    """Pin the additional policy and transport; inherited routes are certified separately."""
    overrides = source_overrides or {}
    if set(overrides) - set(STRATEGY39_SOURCE_AST):
        raise ValueError('Strategy39 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY39_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy39 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy39 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
