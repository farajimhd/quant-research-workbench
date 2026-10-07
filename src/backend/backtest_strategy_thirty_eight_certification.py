"""Reviewed Strategy38 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY38_SOURCE_AST = {'src/trading_runtime/strategy_thirty_eight_release.py': 'd7575dd9cc4281d05f2d2efe0ebbcb43fad882cbcce9e73b419a9e2600f8171c', 'pipelines/strategy_one/strategy_thirty_eight_configuration.py': 'b005887dddacf218efa8591555cfc68b24f72c92a24c6042d3a386f56dc6fdd2', 'src/trading_runtime/strategy_registry.py': '252127231d76a8b0f6ae7a56803bb4d72318ce1e0a45e9280ec086958917b291', 'src/trading_runtime/numbered_fixed_strategy.py': 'a6a6147f4d001a6217512a74cc35fabd51918984eacd43e7800b4ba2ba94dd0b', 'src/backend/backtest_strategy_one_configuration.py': 'b03b4997a5809fffcddd6f4ee84dac9932c6c6cda7cd5581865172a930dd6f0c', 'pipelines/strategy_one/configuration_publisher.py': '96ed50b5e3097d1e4006a9e4b0f1bb4a3263d83fd1cfa4ef61ee56f76de23fdf', 'scripts/clickhouse/publish_strategy_thirty_eight_configuration.py': 'c953d04c7490557986d3786d8fbe4a8b7086d5cd5d9104ad097336fe8710f3e5', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


def certify_strategy_thirty_eight_source(*, source_overrides=None):
    """Pin the additional policy and transport; inherited routes are certified separately."""
    overrides = source_overrides or {}
    if set(overrides) - set(STRATEGY38_SOURCE_AST):
        raise ValueError('Strategy38 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY38_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy38 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            from .backtest_fixed_v4_certification import _reviewed_fixed_lot_profit_projection
            if not _reviewed_fixed_lot_profit_projection(source, relative, '__module__', expected):
                raise ValueError('Strategy38 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
