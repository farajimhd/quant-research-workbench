"""Reviewed Strategy42 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY42_SOURCE_AST = {'src/trading_runtime/strategy_forty_two_release.py': 'ae2e532ddaf0adc3b40ad77a40f8d3fd9cbd6154c344671a17e66545cc81656f', 'pipelines/strategy_one/strategy_forty_two_configuration.py': '7c046aa96f8789a5626b564c25891375e0481e0ccb342a1c7e0f1019268db40c', 'src/trading_runtime/strategy_registry.py': 'ebc3dd4112a8c37b1219f5e5d1207cb12a28a84d9c527236a985e43c480ae919', 'src/trading_runtime/numbered_fixed_strategy.py': 'c0159d3cb5e4bdd503350f18ea9871bb3ad8d26722cae46222faed68c099eabf', 'src/backend/backtest_strategy_one_configuration.py': '753a33078263f96c709151a26e3938bc401c6f07f042ef2d5d18067d653e4c13', 'pipelines/strategy_one/configuration_publisher.py': '72e52c4bbca9c2e97183ffd494089d3f4c09fe1f7da0c7de1d7f0aa3db063941', 'scripts/clickhouse/publish_strategy_forty_two_configuration.py': 'a6f006f9f593640c949d74bbb8a918ecbc9dae8390567076ae54aa1c4ebdc8ea', 'src/trading_runtime/entry_momentum_growth.py': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c', 'src/backend/backtest_declared_initial_momentum.py': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


def certify_strategy_forty_two_source(*, source_overrides=None):
    """Pin the additional policy and transport; inherited routes are certified separately."""
    overrides = source_overrides or {}
    if set(overrides) - set(STRATEGY42_SOURCE_AST):
        raise ValueError('Strategy42 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY42_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy42 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy42 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
