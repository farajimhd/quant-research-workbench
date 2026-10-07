"""Reviewed Strategy38 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY38_SOURCE_AST = {'src/trading_runtime/strategy_thirty_eight_release.py': 'd7575dd9cc4281d05f2d2efe0ebbcb43fad882cbcce9e73b419a9e2600f8171c', 'pipelines/strategy_one/strategy_thirty_eight_configuration.py': 'b005887dddacf218efa8591555cfc68b24f72c92a24c6042d3a386f56dc6fdd2', 'src/trading_runtime/strategy_registry.py': '42ba3dcd3091b55ea67485f3b55b05063895ed0e5ca1fa08334c231a608c07a2', 'src/trading_runtime/numbered_fixed_strategy.py': '96d118e8a2ad954cb3e79b742df4634d07bae2b81913036ae78737285bd7954d', 'src/backend/backtest_strategy_one_configuration.py': 'b1dd5b7ce10fe6597635c98ef3650ae68f2ab9a62281ec5a04015323d97de1f9', 'pipelines/strategy_one/configuration_publisher.py': '3a1579bfdbb0f329282964609372a58d05d8d78da8c46d928d8a6c7a42fe4bed', 'scripts/clickhouse/publish_strategy_thirty_eight_configuration.py': 'c953d04c7490557986d3786d8fbe4a8b7086d5cd5d9104ad097336fe8710f3e5', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


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
