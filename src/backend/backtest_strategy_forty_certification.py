"""Reviewed Strategy40 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY40_SOURCE_AST = {'src/trading_runtime/strategy_forty_release.py': '9c9ca6c91608c67c53466f5f806b2382961be0273d33d1b839e8cf26105b356e',
 'pipelines/strategy_one/strategy_forty_configuration.py': 'c104e52584cf74c473c724e94a5bd9aa84e1d8a9900a144c008b13911212f0cb',
 'src/trading_runtime/strategy_registry.py': '0d20e3a0f38ee2217ae7b0127f862e969620800e47adf3a25d9b92703040f487',
 'src/trading_runtime/numbered_fixed_strategy.py': 'c250b9841885e58d3b690feeb8aa9e52415d4cb2b0fa19e8259a8a603b4a59ea',
 'src/backend/backtest_strategy_one_configuration.py': '930a7b1c497bdf4fe1cda033f46d1857f497697c075dcb6f0925112b398675bd',
 'pipelines/strategy_one/configuration_publisher.py': '355d12708d8779dd79cc7a7994a6731512c3c46ff739a728d08072865b0e5083',
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
