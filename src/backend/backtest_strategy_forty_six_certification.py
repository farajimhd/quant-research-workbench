"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY46_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89', 'src/trading_runtime/declared_followthrough_failure.py': 'ad60271c51250ee004e00e5fc00f8bd8e220794968a9421ec0ff73bd002d5c98', 'src/trading_runtime/strategy_forty_six_release.py': '5d172767cb658cb48eee2d1e5072f07d09163623bc52fc0236fb0f9ed29d66c9', 'src/trading_runtime/strategy_followthrough_exit.py': '85057326d73128bfe04f23fdd72f433693aa49b5837a06354bdba1e5a76b628b', 'src/trading_runtime/arte_followthrough_failure_v4.py': '2ce1e6805a9db71fa5afe0ec63aba61a7d87fc95e3847b89bd7e94b93aa1bc6c', 'src/trading_runtime/strategy_registry.py': '570c8d53eb1133a5b14f403b71d8804970f16c65aede3d12a8ae05ee579fbc13', 'src/trading_runtime/numbered_fixed_strategy.py': '96d118e8a2ad954cb3e79b742df4634d07bae2b81913036ae78737285bd7954d', 'src/backend/backtest_strategy_one_management.py': '7ac9b315872470ac627a6a623cf683d2260cd07ccc5a516ae6698c2b60d89833', 'src/backend/backtest_strategy_one_configuration.py': '6a7720a1ce6223e3fff3364d95a3524442c8d37ad2dea0bff121b5eaab750f29', 'pipelines/strategy_one/configuration_publisher.py': 'fe6f71767e450e7a6c8c7f59dd2fbdf309b095f6a7de9e3342f25292176b6fc6', 'pipelines/strategy_one/strategy_forty_six_configuration.py': 'f63dbde659b84a357edb967e5fb91ff9643d1367aca3d5f940838826ce7a7a48', 'scripts/clickhouse/publish_strategy_forty_six_configuration.py': '24f289afc06c434acadf640624f1e0722676daeb50dc26e4e6db5c8712f1972b', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
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
