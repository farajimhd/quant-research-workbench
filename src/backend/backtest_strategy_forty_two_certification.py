"""Reviewed Strategy42 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY42_SOURCE_AST = {'src/trading_runtime/strategy_forty_two_release.py': 'ae2e532ddaf0adc3b40ad77a40f8d3fd9cbd6154c344671a17e66545cc81656f',
 'pipelines/strategy_one/strategy_forty_two_configuration.py': '7c046aa96f8789a5626b564c25891375e0481e0ccb342a1c7e0f1019268db40c',
 'src/trading_runtime/strategy_registry.py': '0d20e3a0f38ee2217ae7b0127f862e969620800e47adf3a25d9b92703040f487',
 'src/trading_runtime/numbered_fixed_strategy.py': 'c250b9841885e58d3b690feeb8aa9e52415d4cb2b0fa19e8259a8a603b4a59ea',
 'src/backend/backtest_strategy_one_configuration.py': '930a7b1c497bdf4fe1cda033f46d1857f497697c075dcb6f0925112b398675bd',
 'pipelines/strategy_one/configuration_publisher.py': '355d12708d8779dd79cc7a7994a6731512c3c46ff739a728d08072865b0e5083',
 'scripts/clickhouse/publish_strategy_forty_two_configuration.py': 'a6f006f9f593640c949d74bbb8a918ecbc9dae8390567076ae54aa1c4ebdc8ea'}


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
