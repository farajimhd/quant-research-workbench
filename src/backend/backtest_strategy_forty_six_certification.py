"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY46_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89',
 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920',
 'src/trading_runtime/strategy_forty_six_release.py': '5d172767cb658cb48eee2d1e5072f07d09163623bc52fc0236fb0f9ed29d66c9',
 'src/trading_runtime/strategy_followthrough_exit.py': '2467b54df4bacefead61db45065dc90aba6879559f8f1373fbc742834daf34c2',
 'src/trading_runtime/arte_followthrough_failure_v4.py': '991107a7d1f30074fabe4a58fe4b0812b6af42d855bcbb2eb617ff9a765f57b5',
 'src/trading_runtime/strategy_registry.py': '92a48a835437bd4678381a3587299c4e766b2f449bdf0510f43b115fe873fb60',
 'src/trading_runtime/numbered_fixed_strategy.py': 'd09416b3fd0398aaed3bfa3e5d03f2fb5e3dfc8e13a0a216cad0fc1f72a9b773',
 'src/backend/backtest_strategy_one_management.py': '81b15b9bb8f3feae00e4439f8edbbde89cfc3e9a445695bca70da1903010a05c',
 'src/backend/backtest_strategy_one_configuration.py': 'b01f2373be42bfad44440e742b8c5476f12512afa1a7834a089f7175238799e9',
 'pipelines/strategy_one/configuration_publisher.py': '425c546664cdbfc6f01dbd153c90014de1e260c3494033d790621ef81471662c',
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
