"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY48_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1481e0d26e601094b8e699abe8634e900b41f361b777ccbbf7012ae49f1a8e68',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_eight_release.py': 'f844548b2bcba89b371946767beec379005f850a7ef0f037c925d53b6f2ea7bf',
 'src/trading_runtime/strategy_followthrough_exit.py': 'cc77e2075c848c57ee85334d4eb55a73a1ab2c8d624845919861a0a6a7f0d145',
 'src/trading_runtime/arte_followthrough_failure_v4.py': 'b9f177e75083cb7290b10ca1a64b9f4d0b1e5edefc43060c310139fc9ad3a81f',
 'src/trading_runtime/strategy_registry.py': '26edcb6ae79806ee700e50bc3ca771aa5004dcb8c58a6ab6ea3d116feecf4be1',
 'src/trading_runtime/numbered_fixed_strategy.py': 'f9cdbb5afd51b1d9ccad16e42d89b7380c77f7ecad2c48cd785aed63ecaf98b0',
 'src/backend/backtest_strategy_one_management.py': 'f1eaa31e0b38d3ab80759da1e78a23b1f239646ffe4d3fd7a4e3c2dacdd5bb08',
 'src/backend/backtest_strategy_one_configuration.py': '5ea3a8b17b014e119e6ae181202d815fd8307346b5adf3d694f6538d43c1bd5e',
 'pipelines/strategy_one/configuration_publisher.py': '212d1bd2cca308ae71c4a4c711747faf5ea3cc34689e5a0f078984cd3339a54d',
 'pipelines/strategy_one/strategy_forty_eight_configuration.py': '3dcee2e3adb752fc6be0e36edd2f5390659a9a4b29d30d588393863edaff1490',
 'scripts/clickhouse/publish_strategy_forty_eight_configuration.py': '418e0090dd2007b36fcf4f439fce453b5dd299855fa7a11a5bb2fa0d2bfb50b9',
 'src/trading_runtime/arte_entry_activity_v4.py': '2ccca4ffff16da08af0c021c74cb408847255cb81683f4ae122573351de5e6e3',
 'src/trading_runtime/arte_oms_projection.py': '4232070029a03c6b26c0ab266ddea4d447595348c0ed2918deb80753f8c91c56'}


def certify_strategy_forty_eight_source(*, source_overrides=None):
    overrides = source_overrides or {}
    if not STRATEGY48_SOURCE_AST or set(overrides) - set(STRATEGY48_SOURCE_AST):
        raise ValueError('Strategy48 source override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in STRATEGY48_SOURCE_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError('Strategy48 source cannot be parsed: ' + relative) from exc
        if sha256(ast.unparse(tree).encode()).hexdigest() != expected:
            raise ValueError('Strategy48 pinned release source changed: ' + relative)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
