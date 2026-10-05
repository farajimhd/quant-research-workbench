"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY47_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1481e0d26e601094b8e699abe8634e900b41f361b777ccbbf7012ae49f1a8e68',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_seven_release.py': '3a359bd3944fc9d01e9fdd87362b67c7fe5a58728fdb04a3a3a7d33f01c9ca52',
 'src/trading_runtime/strategy_followthrough_exit.py': 'cc77e2075c848c57ee85334d4eb55a73a1ab2c8d624845919861a0a6a7f0d145',
 'src/trading_runtime/arte_followthrough_failure_v4.py': 'b9f177e75083cb7290b10ca1a64b9f4d0b1e5edefc43060c310139fc9ad3a81f',
 'src/trading_runtime/strategy_registry.py': 'c7c4bda74685a3b6197440192e868151eaf4b88d0b35af165380e85f66b21ebd',
 'src/trading_runtime/numbered_fixed_strategy.py': 'aa5b7d49f4b6a0f6a83143c63fd2875fc21bebd202815d652a9d59ed4e6671c1',
 'src/backend/backtest_strategy_one_management.py': 'f1eaa31e0b38d3ab80759da1e78a23b1f239646ffe4d3fd7a4e3c2dacdd5bb08',
 'src/backend/backtest_strategy_one_configuration.py': '2803e66aaeea18c8ef6cd36d9eb7db94c8c69c8de1e867090f7d4d6477df69dd',
 'pipelines/strategy_one/configuration_publisher.py': '3c03d2221a858f87e2cdaaf279668e32450a9586c68600121af74b3638fa0c13',
 'pipelines/strategy_one/strategy_forty_seven_configuration.py': '7a173f89e555c7b32ba160f2433eef69b86cbf6f9b6e2d7c3a6d52b9730481f1',
 'scripts/clickhouse/publish_strategy_forty_seven_configuration.py': 'fab6815de650b142433fd92c90ffc2f90e316b236b145dfae60b7295ac6ec1da',
 'src/trading_runtime/arte_entry_activity_v4.py': '2ccca4ffff16da08af0c021c74cb408847255cb81683f4ae122573351de5e6e3',
 'src/trading_runtime/arte_oms_projection.py': 'cc5f89019c77eb279b776534dc2fd78c68aae85f232b6063901d16a011466a02'}


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
