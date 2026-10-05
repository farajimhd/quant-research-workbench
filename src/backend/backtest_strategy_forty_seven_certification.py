"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY47_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_seven_release.py': '3a359bd3944fc9d01e9fdd87362b67c7fe5a58728fdb04a3a3a7d33f01c9ca52',
 'src/trading_runtime/strategy_followthrough_exit.py': '27857445b8c9aefb6b0fa4a32db5026e4ac25625a4d1db6d9acc9b869eaf5cbe',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '1b984b97b524ccb198de69e7539fb1b6f7574c4a09e502a24b15869b866e140f',
 'src/trading_runtime/strategy_registry.py': '0d20e3a0f38ee2217ae7b0127f862e969620800e47adf3a25d9b92703040f487',
 'src/trading_runtime/numbered_fixed_strategy.py': 'c250b9841885e58d3b690feeb8aa9e52415d4cb2b0fa19e8259a8a603b4a59ea',
 'src/backend/backtest_strategy_one_management.py': 'd73db9cf82446d850817e02aade2ba84fa3925b7364dd6d550753b40f0579448',
 'src/backend/backtest_strategy_one_configuration.py': '930a7b1c497bdf4fe1cda033f46d1857f497697c075dcb6f0925112b398675bd',
 'pipelines/strategy_one/configuration_publisher.py': '355d12708d8779dd79cc7a7994a6731512c3c46ff739a728d08072865b0e5083',
 'pipelines/strategy_one/strategy_forty_seven_configuration.py': '7a173f89e555c7b32ba160f2433eef69b86cbf6f9b6e2d7c3a6d52b9730481f1',
 'scripts/clickhouse/publish_strategy_forty_seven_configuration.py': 'fab6815de650b142433fd92c90ffc2f90e316b236b145dfae60b7295ac6ec1da',
 'src/trading_runtime/arte_entry_activity_v4.py': '9fbd2bcb1ce41a6762ba000439c138318768f472e9502df196831c10d23cf04d',
 'src/trading_runtime/arte_oms_projection.py': '2b4935058811e0f1b70fe091691cf0c5a4e9dd95e56e2083cb0f8a81d77a268e'}


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
