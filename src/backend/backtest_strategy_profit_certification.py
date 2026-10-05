"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '8eb314925f85fa5edebd8110c73f5a57868cdf100e3c150f607470f26cf2ce04'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'a163faa0678e524fce4f5e10e9eb98165a59e3f6c31955f770c2be156d4144e9'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '3de29edec7014e49d1501434c586ba5b5789ce5a05d7a452c1d76ca11db558b2'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '6675e437653e32fc691e6402875e5612a6dcfe65d8ce9e41bda64b249a586890'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '00fad0f28a4f4983a292f2b80faa6f5eea3a4e7299fd83d5844bf02326036ffb'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '3d8c3964c20fed559e753609f9fcbdfaa8e9456fed09cc2f2e5c5e4aabf94446'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'c3dc22f7b20f0e3bab12297a00ad009db83957b7ca1617db5d6863a7a868f444'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '835a3b666e00f437b002d846ec9d486b1e298db48ed5aedd8ebb5a650f2e544b'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'a718f8c5151e33ec2c4243e29b075141cc7cbbbf64b9ad298c5e41c66cfd7533',
                                       '_require_numbered_session_window': 'a410e48cb47a3fb8afbab8b48335a2c311896e4fbb47b8dbf791e7e4f98f2891',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '31601f940a7a1bdf844895cecb667cfc9f22a1cd7543f016d3d4fe9a4e113f22'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'dc286798a43e16b3e2680f48661c5285dba3a091d446d3a98fa8d9746f7536f8'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': 'd97b8a2c841db218bd6f3c752499d738e51682ccbb38a123ef77c8b4b5d23217'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '40188f2b34484692f6f4417641ccc1fbcec3fb264f02cc05abd47a67b8d01c43'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '9b67056857a3f0996238ec5d5eac6822056de1efa389eacce80ff1620a768175'},
 'src/trading_runtime/runtime.py': {'__module__': 'bb0208c12770110b7461144fff08af3cab5890e4b6176b20af1030e8197ded7f'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': 'c824fad3b87d8dceae518a6e11d5747dccb24e1a170bceea166904754cabe6b9'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'ab354ff832ec127a4277aadc075218e2dbd6a36853ed51c6aaaeee45a56ee8c3'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'f32167a2c9582dcbebb66ff27dc80be5b812f45041abb59a0953a92947fd1844'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'd9d33834cd4c28c0dbcfe1474b2173d911633cf3ba179d47d1ddd5a4ac6e9ed5'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '16ea81cdccfe24ba7be1e5533b5904f9e3d389279031a09a4f34688435b22ffc'},
 'src/trading_runtime/strategy_thirty_one_release.py': {'__module__': '2fcd0c7c34073032bf96e2007a901bd8fa99d120d6290a904e13a4e2c94f290e'},
 'src/trading_runtime/strategy_thirty_two_release.py': {'__module__': '66a79fcdd60af4cbf2b7d217340d7b8efa95576ffe694ae05aa597cdd61361a9'},
 'pipelines/strategy_one/strategy_thirty_two_configuration.py': {'__module__': '13a485fb66cdbfab61270b391441d2bb9613c65be77dcfed68eac0494428db7c'},
 'src/trading_runtime/order_management.py': {'cancel_numbered_session_acquisitions': '092a899af5f8fe3cf08b8cb139613350553b6322597be09503f489a0429bb92d'},
 'src/trading_runtime/strategy_thirty_three_release.py': {'__module__': 'cc0afd9027402150723a87f1e66526c7acf35aa78f113d1d5ec57b5675cd386e'},
 'pipelines/strategy_one/strategy_thirty_three_configuration.py': {'__module__': '20dbac5cf28f3f61409c187a9dfcc34b43da02eb81a161ce45ad70f53a4d060c'}}

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
