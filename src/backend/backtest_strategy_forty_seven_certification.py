"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY47_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '9cc399e92aef97628870844e2a6516cbf8df8c6de136be055ccf58f7ae022e68',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_seven_release.py': '3a359bd3944fc9d01e9fdd87362b67c7fe5a58728fdb04a3a3a7d33f01c9ca52',
 'src/trading_runtime/strategy_followthrough_exit.py': '71716c77e5510472df6bebc06995294dfdfa15686589ce01516c9ea2beacc031',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '13faaeab9e5d3673ebeae7f6b3359a8561516581d35a3530ad9cfaa1ece97228',
 'src/trading_runtime/strategy_registry.py': '65220011cea8e502d82fee5bbaf58d856e648ceace15dc48e2d0e9020e81f40d',
 'src/trading_runtime/numbered_fixed_strategy.py': 'f9c42ddd6921012fcf3d761a0fd718a8d207df5601d4440b682b40b0769a0784',
 'src/backend/backtest_strategy_one_management.py': '54815ec98ebcc11913e48e2f215261100a14860990908ee2c9fc2467aede867c',
 'src/backend/backtest_strategy_one_configuration.py': 'e704996f009d645191178125570420d5ff694577eedac9ff731eea0e4f1d8516',
 'pipelines/strategy_one/configuration_publisher.py': '3d5ad0a8e627ca8f2ebdb64d469e5babbb49fd8fb349a1997a861b27185ddb6d',
 'pipelines/strategy_one/strategy_forty_seven_configuration.py': '7a173f89e555c7b32ba160f2433eef69b86cbf6f9b6e2d7c3a6d52b9730481f1',
 'scripts/clickhouse/publish_strategy_forty_seven_configuration.py': 'fab6815de650b142433fd92c90ffc2f90e316b236b145dfae60b7295ac6ec1da',
 'src/trading_runtime/arte_entry_activity_v4.py': 'defa7526754031d5be77617f0baa0fcb81ef3cc68eab28e9c6a5e842a8c6f7fb',
 'src/trading_runtime/arte_oms_projection.py': 'a2eb28260f581a1e482570ad2f6658c678281c13efe76f84474196399c6749c8'}


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
