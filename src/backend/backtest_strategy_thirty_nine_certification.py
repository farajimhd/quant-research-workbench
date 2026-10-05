"""Reviewed Strategy39 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY39_SOURCE_AST = {'src/trading_runtime/strategy_thirty_nine_release.py': '0b3a73c0d34d2810e815f07ab21b1d6e5dd4c128b087c1fea77abb662bebf155',
 'pipelines/strategy_one/strategy_thirty_nine_configuration.py': 'ab868da6d6f1ed4be17564539f894bdb535d64804cc4931fb8c8d07017ae315f',
 'src/trading_runtime/strategy_registry.py': '0d20e3a0f38ee2217ae7b0127f862e969620800e47adf3a25d9b92703040f487',
 'src/trading_runtime/numbered_fixed_strategy.py': 'c250b9841885e58d3b690feeb8aa9e52415d4cb2b0fa19e8259a8a603b4a59ea',
 'src/backend/backtest_strategy_one_configuration.py': '930a7b1c497bdf4fe1cda033f46d1857f497697c075dcb6f0925112b398675bd',
 'pipelines/strategy_one/configuration_publisher.py': '355d12708d8779dd79cc7a7994a6731512c3c46ff739a728d08072865b0e5083',
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
