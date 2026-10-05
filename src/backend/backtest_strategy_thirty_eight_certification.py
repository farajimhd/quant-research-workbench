"""Reviewed Strategy38 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY38_SOURCE_AST = {'src/trading_runtime/strategy_thirty_eight_release.py': 'd7575dd9cc4281d05f2d2efe0ebbcb43fad882cbcce9e73b419a9e2600f8171c',
 'pipelines/strategy_one/strategy_thirty_eight_configuration.py': 'b005887dddacf218efa8591555cfc68b24f72c92a24c6042d3a386f56dc6fdd2',
 'src/trading_runtime/strategy_registry.py': '92a48a835437bd4678381a3587299c4e766b2f449bdf0510f43b115fe873fb60',
 'src/trading_runtime/numbered_fixed_strategy.py': 'd09416b3fd0398aaed3bfa3e5d03f2fb5e3dfc8e13a0a216cad0fc1f72a9b773',
 'src/backend/backtest_strategy_one_configuration.py': 'b01f2373be42bfad44440e742b8c5476f12512afa1a7834a089f7175238799e9',
 'pipelines/strategy_one/configuration_publisher.py': '425c546664cdbfc6f01dbd153c90014de1e260c3494033d790621ef81471662c',
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
