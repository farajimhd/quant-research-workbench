"""Exact reviewed native AH-risk successor; inherited proof remains mandatory."""
import ast
from hashlib import sha256
import json
from pathlib import Path

# Filled only after reviewing the complete owned native route and its tests.
STRATEGY48_SOURCE_AST = {'src/trading_runtime/early_original_risk_failure.py': '1df39278ef45fd7392fa570702b836588c21274aa0eb36c3667477dbcef0dc89', 'src/trading_runtime/declared_followthrough_failure.py': '68c2ed0f236db57cf07b557a331448b77a35de9c0645f5e7ec5adde51e1f9920', 'src/trading_runtime/strategy_forty_eight_release.py': 'f844548b2bcba89b371946767beec379005f850a7ef0f037c925d53b6f2ea7bf', 'src/trading_runtime/strategy_followthrough_exit.py': 'b39c0a150a26245ced193548897600c3dbbff8951271a8013e73832611855d66', 'src/trading_runtime/arte_followthrough_failure_v4.py': '13377a8b4d30488440076af50949db7f4a54c612d02e10391efbf4edb54f68c9', 'src/trading_runtime/strategy_registry.py': 'ebc3dd4112a8c37b1219f5e5d1207cb12a28a84d9c527236a985e43c480ae919', 'src/trading_runtime/numbered_fixed_strategy.py': 'c0159d3cb5e4bdd503350f18ea9871bb3ad8d26722cae46222faed68c099eabf', 'src/backend/backtest_strategy_one_management.py': 'f57ee3ecf81e70f84916fbbd84e2c827f38b510d8c1cce2d11a7ad2007d8a33e', 'src/backend/backtest_strategy_one_configuration.py': '753a33078263f96c709151a26e3938bc401c6f07f042ef2d5d18067d653e4c13', 'pipelines/strategy_one/configuration_publisher.py': '72e52c4bbca9c2e97183ffd494089d3f4c09fe1f7da0c7de1d7f0aa3db063941', 'pipelines/strategy_one/strategy_forty_eight_configuration.py': '3dcee2e3adb752fc6be0e36edd2f5390659a9a4b29d30d588393863edaff1490', 'scripts/clickhouse/publish_strategy_forty_eight_configuration.py': '418e0090dd2007b36fcf4f439fce453b5dd299855fa7a11a5bb2fa0d2bfb50b9', 'src/trading_runtime/arte_entry_activity_v4.py': '95b32e75b49992cb38e0569e1576e093841f46eb3b8defd37e5ec4d0e8494b92', 'src/trading_runtime/arte_oms_projection.py': 'a4514545219759a1ae0c79465531e548ec2e34584c61d1fb7abc09da130893f9', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}


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
