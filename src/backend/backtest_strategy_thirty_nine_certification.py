"""Reviewed Strategy39 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY39_SOURCE_AST = {'src/trading_runtime/strategy_thirty_nine_release.py': '0b3a73c0d34d2810e815f07ab21b1d6e5dd4c128b087c1fea77abb662bebf155',
 'pipelines/strategy_one/strategy_thirty_nine_configuration.py': 'ab868da6d6f1ed4be17564539f894bdb535d64804cc4931fb8c8d07017ae315f',
 'src/trading_runtime/strategy_registry.py': '4d08aee98475473a2772c2619cf69b7232e22a7dbb2373f7a50ead40ab8699d8',
 'src/trading_runtime/numbered_fixed_strategy.py': '0f6a62a9c7096f278caacc6861fff149978497779e4f72f0140d8466ee7389ea',
 'src/backend/backtest_strategy_one_configuration.py': 'e13e33f96c71278223ab8babc839052f3659f2e3bbccbd8beb79ac61deef24ca',
 'pipelines/strategy_one/configuration_publisher.py': '7d18b48bcab46736081384bf1ce994f4e9edda501c22254e4c12d755954f7874',
 'scripts/clickhouse/publish_strategy_thirty_nine_configuration.py': '65985cb49ccd17778f47c970cb0413d2e88b2b7d96c94d67b6640057f9fb97f3'}


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
