"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY47_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_seven_release.py': '3a359bd3944fc9d01e9fdd87362b67c7fe5a58728fdb04a3a3a7d33f01c9ca52',
 'src/trading_runtime/strategy_followthrough_exit.py': '6faac9cae66c139869dad4c3be31d4b9a40efa3e3984bfb8e0bdc088d277d450',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '8a1daf50731f83bd20e32c0e0db4e0f5c3074c4a1030ab87528a7e964b614907',
 'src/trading_runtime/strategy_registry.py': 'a93f5cde8cbf06e0c1af27ff3d8d097f3eb7e869644025dba4fa187f74ebcad4',
 'src/trading_runtime/numbered_fixed_strategy.py': '295c5db3012f008dee16be92633884c974ad2315120752c86c86f5ccf718335d',
 'src/backend/backtest_strategy_one_management.py': '8001c4c43ca9775c3b3b45087c55fc36f9786844f6a19b698cc445393e6cc7db',
 'src/backend/backtest_strategy_one_configuration.py': 'e7ac8ac056735d57086db293f47d915a7a987cf0e0e91d428e7117e494399f3f',
 'pipelines/strategy_one/configuration_publisher.py': 'bebbde79b2ad694092222e96d4e09bd50cb3579295a9ca85bf2fcbcb8ff6dc1f',
 'pipelines/strategy_one/strategy_forty_seven_configuration.py': '7a173f89e555c7b32ba160f2433eef69b86cbf6f9b6e2d7c3a6d52b9730481f1',
 'scripts/clickhouse/publish_strategy_forty_seven_configuration.py': 'fab6815de650b142433fd92c90ffc2f90e316b236b145dfae60b7295ac6ec1da',
 'src/trading_runtime/arte_entry_activity_v4.py': 'd1925f80bf6309e040d257604a7dcaf890e00e471fb822218cfa1418bb1b52d3',
 'src/trading_runtime/arte_oms_projection.py': '2320e8877a34c57a4d3dec38a45a48b540bf0577d995b1837e1246db6a66eed6'}


def certify_strategy_forty_seven_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if not STRATEGY47_SOURCE_AST or set(overrides) - set(STRATEGY47_SOURCE_AST):
        raise ValueError('Strategy47 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY47_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy47 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy47 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
