"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY46_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89', 'src/trading_runtime/declared_followthrough_failure.py': 'ad60271c51250ee004e00e5fc00f8bd8e220794968a9421ec0ff73bd002d5c98', 'src/trading_runtime/strategy_forty_six_release.py': '5d172767cb658cb48eee2d1e5072f07d09163623bc52fc0236fb0f9ed29d66c9', 'src/trading_runtime/strategy_followthrough_exit.py': 'c3e1a4cb00ae48d3f2e11ffb6218dc26a411bc254b6700f36cb613a1bef32d91', 'src/trading_runtime/arte_followthrough_failure_v4.py': '13377a8b4d30488440076af50949db7f4a54c612d02e10391efbf4edb54f68c9', 'src/trading_runtime/strategy_registry.py': '708960fc0d38a859ae97d58f7feac74aa8c8087671b826d3acae0eb4d15a0a9f', 'src/trading_runtime/numbered_fixed_strategy.py': '3292fce6d35bcf81fc3ce284d47ce133b49dbc528c902178f15f40d27cc3bc46', 'src/backend/backtest_strategy_one_management.py': '96c4bc9c6ac5e3041bba14bb382565395d4431549f1e3de98dcb7c8c582c1e1e', 'src/backend/backtest_strategy_one_configuration.py': '6514b72bbb4dc542b958d5d59def72f62491a2ce08acf813f010367e8ce776bb', 'pipelines/strategy_one/configuration_publisher.py': 'd6eedccd686e0d26b39153cf8cb07d753f9fae1f77818cf367b0e30ef6570de1', 'pipelines/strategy_one/strategy_forty_six_configuration.py': 'f63dbde659b84a357edb967e5fb91ff9643d1367aca3d5f940838826ce7a7a48', 'scripts/clickhouse/publish_strategy_forty_six_configuration.py': '24f289afc06c434acadf640624f1e0722676daeb50dc26e4e6db5c8712f1972b', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
    'src/trading_runtime/all_held_original_risk_failure.py': '093bb7f1b73277f21aa1a399a7b3b9f36d77dea8ca614f1130314029fcfaf8cf'}


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
