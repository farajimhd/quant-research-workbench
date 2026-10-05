"""Reviewed Strategy41 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY41_SOURCE_AST = {'src/trading_runtime/strategy_forty_one_release.py': '8cfd72152770987792482af4328c8516b1b05541ac521003d96993169bd57848',
 'pipelines/strategy_one/strategy_forty_one_configuration.py': '9db0d8f7898a3c821dce8753a004182daa2c633f023ea6f002d4d853681fe761',
 'src/trading_runtime/strategy_registry.py': '92a48a835437bd4678381a3587299c4e766b2f449bdf0510f43b115fe873fb60',
 'src/trading_runtime/numbered_fixed_strategy.py': 'd09416b3fd0398aaed3bfa3e5d03f2fb5e3dfc8e13a0a216cad0fc1f72a9b773',
 'src/backend/backtest_strategy_one_configuration.py': 'b01f2373be42bfad44440e742b8c5476f12512afa1a7834a089f7175238799e9',
 'pipelines/strategy_one/configuration_publisher.py': '425c546664cdbfc6f01dbd153c90014de1e260c3494033d790621ef81471662c',
 'scripts/clickhouse/publish_strategy_forty_one_configuration.py': '1c9d39b724ebeb0a0a097d7d69f9af5203fdbf8581d26815b851c70528cd78f2'}


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
