"""Reviewed Strategy40 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY40_SOURCE_AST = {'src/trading_runtime/strategy_forty_release.py': '9c9ca6c91608c67c53466f5f806b2382961be0273d33d1b839e8cf26105b356e',
 'pipelines/strategy_one/strategy_forty_configuration.py': 'c104e52584cf74c473c724e94a5bd9aa84e1d8a9900a144c008b13911212f0cb',
 'src/trading_runtime/strategy_registry.py': '4d08aee98475473a2772c2619cf69b7232e22a7dbb2373f7a50ead40ab8699d8',
 'src/trading_runtime/numbered_fixed_strategy.py': '0f6a62a9c7096f278caacc6861fff149978497779e4f72f0140d8466ee7389ea',
 'src/backend/backtest_strategy_one_configuration.py': 'e13e33f96c71278223ab8babc839052f3659f2e3bbccbd8beb79ac61deef24ca',
 'pipelines/strategy_one/configuration_publisher.py': '7d18b48bcab46736081384bf1ce994f4e9edda501c22254e4c12d755954f7874',
 'scripts/clickhouse/publish_strategy_forty_configuration.py': 'f14db18ef05f42c90ff7d4dbc58b366084efd2ad9dcafc66009958b81aa8ff0b'}


def certify_strategy_forty_source(*, source_overrides=None):
    """Pin the additional policy and transport; inherited routes are certified separately."""
    overrides = source_overrides or {}
    if set(overrides) - set(STRATEGY40_SOURCE_AST):
        raise ValueError('Strategy40 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY40_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy40 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy40 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
