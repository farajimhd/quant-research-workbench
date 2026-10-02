"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '6fff67894cd08c65969b9ebf26e58db942db8f6d80fb588b3e93c9544ace8a2f'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': '824a8f8803af1d06073f509644d25cb50052379f24a259adc7ac819920d9e006'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': 'f94b8fc15d933791948cc2e136ab835ca810fe93abcb483668d5639f5dd14b0c'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '7963a7e094838be998606b5a088b046cfaa8066e3ee0602cd9bb2bd9f890da01'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': '682fc5080071a210aaca1b6f91fdd2cebf8c4fe11f05c4e3aa5c94f7ba2809cb'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '001e1108165718d1df344072ee7dbcaef6d08fbf3fdc2d2b6bcec9e6117dd0c6'},
 'src/backend/backtest_typed_projection.py': {'__module__': 'bd282ce151c36235eb3a881ecb14c26a6b463d35ebd934d9420b9fa015bf8af9'},
 'src/backend/backtest_typed_publisher.py': {'__module__': 'c668f99f94a5c52da8fced7cb10c8c33bd1f32e76f784b7853e956db891f3613'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'fa5cc72ca024e2fecdf666c1a10db4d9df509b1ed9760a5fc88338cce3157088',
                                       '_require_numbered_session_window': 'c6f110d87324ae425ef8e988e3ffc1ffa439ad17a797b47e7b3b2f11099eb3b9',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': '49c7ad6cb379143fb52b2d0cb0016696662884ed69a5cdee45a1cf4c27a3072b'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': 'ec1faf4bde235da17a4873bbf6db4853be8751c2bce45d39f4637c3565bb7b7b'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '16841678f27bfa15b475978a8bff89a0ba822c44aaafb7f59214c5498860f4af'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': '665ef3655d5a912de59e669159e4c64fc384c2f048f36b8a95b97f7bdf627575'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': 'a2f4d21fd878c2ac53088a45589633c942989e41ff085ef70fd83940797b9231'},
 'src/trading_runtime/runtime.py': {'__module__': 'c6dd993c3fa5b32bb0cf0414d0df1f92c487aff7dfeb9d9c1374f24cc89cb2b5'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '25698c2a64d42a173a5e4e5f5a5433c3bedccb0f825730a16b04a6b3f515f844'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '4a2f5ea411642e446f6b4cd71922676964fe773980b2f88c96efd66aee81585a'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': '7334979286692791d71e19800838b0b8093c3e207a4b39346d2b43c2f9f24cf3'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': '3bc702ba3f6d87e46f87322cb5865350f90be2c59f9b129c7eaf3cdc7cbdf4d9'},
 'src/trading_runtime/strategy_registry.py': {'__module__': '7292328bd2dbc0b7f84bf31758a88ad6203ecce5845c268305ea7150d434d314'},
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
