"""Reviewed Strategy40 exact-parent registration and compiler source authority."""
import ast
from hashlib import sha256
import json
from pathlib import Path

STRATEGY40_SOURCE_AST = {'src/trading_runtime/strategy_forty_release.py': '9c9ca6c91608c67c53466f5f806b2382961be0273d33d1b839e8cf26105b356e',
 'pipelines/strategy_one/strategy_forty_configuration.py': 'c104e52584cf74c473c724e94a5bd9aa84e1d8a9900a144c008b13911212f0cb',
 'src/trading_runtime/strategy_registry.py': '5d67e5a268fb2b07824c733033a9907237ba05dc962289cc51f3262333dc1efe',
 'src/trading_runtime/numbered_fixed_strategy.py': 'e77afb232629caaf7cd736f3e42ffda9e71690003fd3943b690b142742c55d6f',
 'src/backend/backtest_strategy_one_configuration.py': 'd5a2bc061571e09ae55ea611e7e99f5e608f9ab9dec7e350d2742d7fca3224c9',
 'pipelines/strategy_one/configuration_publisher.py': '2bda267ca9ea242d6be5d30460d0d66c37b3bf6935867343a201b8f20d03f00b',
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
