"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY46_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_six_release.py': '5d172767cb658cb48eee2d1e5072f07d09163623bc52fc0236fb0f9ed29d66c9',
 'src/trading_runtime/strategy_followthrough_exit.py': '68ede578ca6668621ba44b7c9bf1c298f8c963de8bd05a3786942e2de4a3447b',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '5e69522f0843ec8e275a6b8ef8066609f72e082391e76089cecff7dd66c042a8',
 'src/trading_runtime/strategy_registry.py': '5d67e5a268fb2b07824c733033a9907237ba05dc962289cc51f3262333dc1efe',
 'src/trading_runtime/numbered_fixed_strategy.py': 'e77afb232629caaf7cd736f3e42ffda9e71690003fd3943b690b142742c55d6f',
 'src/backend/backtest_strategy_one_management.py': '5eba2fbe247ced68f8d62ff653ceb68fa1486e8e0dbdf00140c88f32604b10c5',
 'src/backend/backtest_strategy_one_configuration.py': 'd5a2bc061571e09ae55ea611e7e99f5e608f9ab9dec7e350d2742d7fca3224c9',
 'pipelines/strategy_one/configuration_publisher.py': '2bda267ca9ea242d6be5d30460d0d66c37b3bf6935867343a201b8f20d03f00b',
 'pipelines/strategy_one/strategy_forty_six_configuration.py': 'f63dbde659b84a357edb967e5fb91ff9643d1367aca3d5f940838826ce7a7a48',
 'scripts/clickhouse/publish_strategy_forty_six_configuration.py': '24f289afc06c434acadf640624f1e0722676daeb50dc26e4e6db5c8712f1972b'}


def certify_strategy_forty_six_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if not STRATEGY46_SOURCE_AST or set(overrides) - set(STRATEGY46_SOURCE_AST):
        raise ValueError('Strategy46 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY46_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy46 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy46 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
