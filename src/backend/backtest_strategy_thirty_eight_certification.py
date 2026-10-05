"""Reviewed Strategy38 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY38_SOURCE_AST = {'src/trading_runtime/strategy_thirty_eight_release.py': 'd7575dd9cc4281d05f2d2efe0ebbcb43fad882cbcce9e73b419a9e2600f8171c',
 'pipelines/strategy_one/strategy_thirty_eight_configuration.py': 'b005887dddacf218efa8591555cfc68b24f72c92a24c6042d3a386f56dc6fdd2',
 'src/trading_runtime/strategy_registry.py': '26edcb6ae79806ee700e50bc3ca771aa5004dcb8c58a6ab6ea3d116feecf4be1',
 'src/trading_runtime/numbered_fixed_strategy.py': 'f9cdbb5afd51b1d9ccad16e42d89b7380c77f7ecad2c48cd785aed63ecaf98b0',
 'src/backend/backtest_strategy_one_configuration.py': '5ea3a8b17b014e119e6ae181202d815fd8307346b5adf3d694f6538d43c1bd5e',
 'pipelines/strategy_one/configuration_publisher.py': '212d1bd2cca308ae71c4a4c711747faf5ea3cc34689e5a0f078984cd3339a54d',
 'scripts/clickhouse/publish_strategy_thirty_eight_configuration.py': 'c953d04c7490557986d3786d8fbe4a8b7086d5cd5d9104ad097336fe8710f3e5'}


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
            raise ValueError('Strategy38 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
