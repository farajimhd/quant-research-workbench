"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY46_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_six_release.py': '5d172767cb658cb48eee2d1e5072f07d09163623bc52fc0236fb0f9ed29d66c9',
 'src/trading_runtime/strategy_followthrough_exit.py': 'cfac0ff13329deaa3d42cdbcd44035c46f52a1a9d00ea9649024a629b2dad4fc',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '6601fcafd78180d4eec1141df586fd918177db44b079ffbb6605e70885f090f7',
 'src/trading_runtime/strategy_registry.py': '56a9a7e178c7c062e7948ba495621e250572c15bd5063fff960e8c41a5ead0c8',
 'src/trading_runtime/numbered_fixed_strategy.py': '59e01acb2138459dfa819c6853b8096688c15d9307ed03e2b1e841ebe01042ab',
 'src/backend/backtest_strategy_one_management.py': 'c4d3722667eb5024f09086f5e65ce258361028993a65eef2de6af69ff4f645a9',
 'src/backend/backtest_strategy_one_configuration.py': '9fc30e92b34bb1690625dd63a3cd18bd0ddfb89497fd1df33c67bfec804fcb21',
 'pipelines/strategy_one/configuration_publisher.py': 'b6a7ce44ff0729b0eac8aa0dd0f2eac355661b9c1a76f1d94f159be28c5813d1',
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
