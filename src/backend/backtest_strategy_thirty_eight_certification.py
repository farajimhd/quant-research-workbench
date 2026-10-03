"""Reviewed Strategy38 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY38_SOURCE_AST = {'src/trading_runtime/strategy_thirty_eight_release.py': 'd7575dd9cc4281d05f2d2efe0ebbcb43fad882cbcce9e73b419a9e2600f8171c', 'pipelines/strategy_one/strategy_thirty_eight_configuration.py': 'b005887dddacf218efa8591555cfc68b24f72c92a24c6042d3a386f56dc6fdd2', 'src/trading_runtime/strategy_registry.py': 'd6b06838efb9d86db9e8ccf258c0d434913a28a583cae0dd7bfa9dfbd4bfe842', 'src/trading_runtime/numbered_fixed_strategy.py': '4572b8612247111a011c396f39ef14d879dca99b6988a81b0cc5a60f9103e861', 'src/backend/backtest_strategy_one_configuration.py': 'd75eed6ac5a3404930474e524bff5f905ee4dc42cae83c6d27444a50f56b45df', 'pipelines/strategy_one/configuration_publisher.py': '571e90ade71025ce0fb71cea99895c0e20145b12dbefc1b2398efbd7a2601cbc', 'scripts/clickhouse/publish_strategy_thirty_eight_configuration.py': 'c953d04c7490557986d3786d8fbe4a8b7086d5cd5d9104ad097336fe8710f3e5'}


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
            from .backtest_historical_strategy_projection import historical_strategy_tree
            tree = historical_strategy_tree(ast.parse(source), relative)
        except SyntaxError as exc:
            raise ValueError('Strategy38 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy38 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
