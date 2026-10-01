"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '01cc6bd7baa7e1c30e352c71dd722d5e4511e59b6e1e8031b2be52bacce108e7'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '4a40c0058e1bfd6061f2e7980b1e0cecf03559cdd4678d6cdbd46c134d3cb0c1'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '9dfb89129a3c60d8c4eefa1d36f89ef6bc5c624c4c8106e474c5606e783ceaff'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '4f6773e7a0ef4b21089403372920695b9571cfd2e9f8cb3ef46512b5b90f8512'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'cb90a2aded6c6e4d49f6e7e7fe9b84d2e48201c33f9ee9eb0d046e6b9912daa5'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': 'de9072c2cbe8f086304a1a98e9e4a1013f6f71dfb863d5f45b17821afc37cfc2'},
 'src/backend/backtest_typed_projection.py': {'__module__': '0d435c45d49701f3afeee67afbea29701672ccfe2532ea169a9ab22ae914f205'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '3f46e237baecaabf2f483a823a9621b116c1ac287ba8dce098533094ac67f4cc'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'ae4fa9304039b04e0fed10e84350052e4fd83435188b6551fe5cc16c4fc70994',
                                       '_require_numbered_session_window': '663fe9ed8868073668fa35e9208e4f052f1eb1d66e150a3cfac0790591f13db8',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '420fcc6c177b671bf9c9da19992fd17e8c37915dafe93e9330d1520fd85eaa91'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '90bacc9a2c28335a1b33380f05e9d572a06ff0fce2fff2205647b363cc08b507'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '7ed9bffec3b5a955028c01bec7664fe0ddfe9145b58652af8a0a6d967124c04f'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '8bb34a818e7a74b015d313eabb6d3f1821e4a8ab5daeea349fdb459d7eb4dbd6'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '73789946a52497656930ebdf084dee98d876452e79ff5af34ba6fa0918c10f98'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '4075ee85ce0b0f92a8403abb89d41b5378691db45973ec8c701b99f4d5cc82e9'},
 'src/trading_runtime/runtime.py': {'__module__': '35148a270015ad12da30868bcd8a296f804eab426792599d206542bca70c5363'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'e9720090b7cfa0866532b297cd229cb4401575f7f2768c147bba4a60b765119b'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '12cbafee5545044810bd6ea5c3d68a6c8cd605c5ce5afdf25494cfdd31f5cbf8'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'd1424caa36a82430cc4ab0930f0d8a38e2556bd1e9a2a27798ee97edec49f896'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'f6af3e598ccf1db2d48bedf81910945f13cafe43078eb549ddf67a1aea18453f'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '009ecc3412bbad8f0186acfa7f93d2532788d6c22391e725a6f199955fca506d'},
 'src/trading_runtime/strategy_thirty_one_release.py': {'__module__': '2fcd0c7c34073032bf96e2007a901bd8fa99d120d6290a904e13a4e2c94f290e'}}

def certify_profit_giveback_route_source(*, source_overrides=None):
    """Fail closed if any reviewed arming, order or persistence route changes."""
    overrides = source_overrides or {}
    if set(overrides) - set(REVIEWED_PROFIT_ROUTE):
        raise ValueError('Profit route override is outside reviewed authority')
    root = Path(__file__).parents[2]
    observed = []
    for relative, expected in REVIEWED_PROFIT_ROUTE.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding='utf-8')
        tree = ast.parse(source)
        for name, digest in expected.items():
            nodes = [tree] if name == '__module__' else [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
            if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                raise ValueError('Strategy 31 reviewed profit-route authority changed: ' + relative + ':' + name)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(',', ':')).encode()).hexdigest()
