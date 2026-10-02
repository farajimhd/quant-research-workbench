"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '80081bad3f76f67437dcb8a507cb28551bbddac332ee9cd40bc53934273e48b5'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'aebc9e470ca185086d847cb9b973318b88d5009844b79ed13c05be4a6e4b67d6'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'a3157672b364f52abfbfe66d8ebdd0bda3e9f87506ef396349562644f83f37bf'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '6b4b10391952a7b4082bd35a7a10b5b909c78015e661a7b5d30a865a44cebe83'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '1aeade532dfc89b8f319b64082609e586ff44d4a636ff8f04ce706289b469f9a'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '2afd93a156ce2925ae93c4705687851876b8b4a0c59e052a787caa9fd48081ad'},
 'src/backend/backtest_typed_projection.py': {'__module__': '7593347516c3365103d1f254cf0068178cf492f4d0184f7ff804b10c4c4e7886'},
 'src/backend/backtest_typed_publisher.py': {'__module__': '0e346fc1352cd0c4e0fdaf76a7a4c110df5d4b0eec3ba3ec019496bdc8703a49'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': '12db9b476ac9bf366006f2c6895d749681605cd290012d56e5d7be0aa2c47bee',
                                       '_require_numbered_session_window': 'ea5982b04744cca053c800aa2bb1960255fa30073745275b91ba8f88452b6fd8',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'f040d5a4e83f5d60488338ba94a32b226709b82a2db812472c163fd3b80ca6f2'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4965c541673e52dffff094d170ef4a255f58fd2eeb154c3bfa2bf34c78b14c8b'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'a7807c9ae461f72e9b00647ffb865f9a8f62e3c1f5c24657263084af2756b732'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '45fe16d3f2f1e9411230656fdbab0d3a3cc7bde956836b0752b912bd6b88bac3'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '3410ccea46575c540e58036cf537f72a26cc47e50ba5f75664722b62748fe7de'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'fd1f4afae0ea2d0b3604c4bffb76a7d28e0132f470de587db4380c2ee8c0898f'},
 'src/trading_runtime/runtime.py': {'__module__': 'b8da3a1e6a65cd5419335cfc9af9e065a57ab76b7ba3f9bde31ab08681388729'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '09f7001316e46490ad19b85d0b0217cdf092f4cad4dd89c9d5902e3a49ad684c'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'eb21ab1d2a25c5d411f4c6c1e109fb9f1e5290e1727585f49dbb5279d213059e'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'e459143bcdc9d1e2b58af0dd842df3dae6d21dada0e8b3d11a108a50f5fdaa6a'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'e8b5daf8cafdfbede62b3ed042956554494e1dac840a48fb30d3995d83126f42'},
 'src/trading_runtime/strategy_registry.py': {'__module__': 'e04450c9bbbc4a6b3b312a820735c38b7c2dc9fe323aa6d087853c833d3b2ca9'},
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
