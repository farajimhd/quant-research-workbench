"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY46_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89', 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920', 'src/trading_runtime/strategy_forty_six_release.py': '5d172767cb658cb48eee2d1e5072f07d09163623bc52fc0236fb0f9ed29d66c9', 'src/trading_runtime/strategy_followthrough_exit.py': 'b39c0a150a26245ced193548897600c3dbbff8951271a8013e73832611855d66', 'src/trading_runtime/arte_followthrough_failure_v4.py': '13377a8b4d30488440076af50949db7f4a54c612d02e10391efbf4edb54f68c9', 'src/trading_runtime/strategy_registry.py': 'ebc3dd4112a8c37b1219f5e5d1207cb12a28a84d9c527236a985e43c480ae919', 'src/trading_runtime/numbered_fixed_strategy.py': 'c0159d3cb5e4bdd503350f18ea9871bb3ad8d26722cae46222faed68c099eabf', 'src/backend/backtest_strategy_one_management.py': 'f57ee3ecf81e70f84916fbbd84e2c827f38b510d8c1cce2d11a7ad2007d8a33e', 'src/backend/backtest_strategy_one_configuration.py': '753a33078263f96c709151a26e3938bc401c6f07f042ef2d5d18067d653e4c13', 'pipelines/strategy_one/configuration_publisher.py': '72e52c4bbca9c2e97183ffd494089d3f4c09fe1f7da0c7de1d7f0aa3db063941', 'pipelines/strategy_one/strategy_forty_six_configuration.py': 'f63dbde659b84a357edb967e5fb91ff9643d1367aca3d5f940838826ce7a7a48', 'scripts/clickhouse/publish_strategy_forty_six_configuration.py': '24f289afc06c434acadf640624f1e0722676daeb50dc26e4e6db5c8712f1972b', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


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
