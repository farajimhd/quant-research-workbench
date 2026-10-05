"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY48_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_eight_release.py': 'f844548b2bcba89b371946767beec379005f850a7ef0f037c925d53b6f2ea7bf',
 'src/trading_runtime/strategy_followthrough_exit.py': 'cfac0ff13329deaa3d42cdbcd44035c46f52a1a9d00ea9649024a629b2dad4fc',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '6601fcafd78180d4eec1141df586fd918177db44b079ffbb6605e70885f090f7',
 'src/trading_runtime/strategy_registry.py': '56a9a7e178c7c062e7948ba495621e250572c15bd5063fff960e8c41a5ead0c8',
 'src/trading_runtime/numbered_fixed_strategy.py': '59e01acb2138459dfa819c6853b8096688c15d9307ed03e2b1e841ebe01042ab',
 'src/backend/backtest_strategy_one_management.py': 'c4d3722667eb5024f09086f5e65ce258361028993a65eef2de6af69ff4f645a9',
 'src/backend/backtest_strategy_one_configuration.py': '9fc30e92b34bb1690625dd63a3cd18bd0ddfb89497fd1df33c67bfec804fcb21',
 'pipelines/strategy_one/configuration_publisher.py': 'b6a7ce44ff0729b0eac8aa0dd0f2eac355661b9c1a76f1d94f159be28c5813d1',
 'pipelines/strategy_one/strategy_forty_eight_configuration.py': '3dcee2e3adb752fc6be0e36edd2f5390659a9a4b29d30d588393863edaff1490',
 'scripts/clickhouse/publish_strategy_forty_eight_configuration.py': '418e0090dd2007b36fcf4f439fce453b5dd299855fa7a11a5bb2fa0d2bfb50b9',
 'src/trading_runtime/arte_entry_activity_v4.py': 'baa2f4ce4dcb37d23d720363e18b21790c7fb545d254544ad723a6db4a11601e',
 'src/trading_runtime/arte_oms_projection.py': 'b31004ffbfa21fb7a697e89031911a853175033597903ea9e2ca2891ccebe35a'}


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
