"""Reviewed Strategy40 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY40_SOURCE_AST = {'src/trading_runtime/strategy_forty_release.py': '9c9ca6c91608c67c53466f5f806b2382961be0273d33d1b839e8cf26105b356e', 'pipelines/strategy_one/strategy_forty_configuration.py': 'c104e52584cf74c473c724e94a5bd9aa84e1d8a9900a144c008b13911212f0cb', 'src/trading_runtime/strategy_registry.py': '9e062e691c9f32b94a160e657b2401839334e317deb8ba69fbd7f1d70ac3163e', 'src/trading_runtime/numbered_fixed_strategy.py': '96d118e8a2ad954cb3e79b742df4634d07bae2b81913036ae78737285bd7954d', 'src/backend/backtest_strategy_one_configuration.py': '92b234c6d4139ed52f25cb2e713715e814db050eec5713eb7984240646821ba5', 'pipelines/strategy_one/configuration_publisher.py': '240e24684ba603a852d179dd6a741d545aa877c6911e33d071a638100b4876d9', 'scripts/clickhouse/publish_strategy_forty_configuration.py': 'f14db18ef05f42c90ff7d4dbc58b366084efd2ad9dcafc66009958b81aa8ff0b', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


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
