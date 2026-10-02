"""Exact reviewed profit-route sources; native installation is a separate gate."""
import ast
from hashlib import sha256
from pathlib import Path
import json

REVIEWED_PROFIT_ROUTE = {'pipelines/strategy_one/configuration_publisher.py': {'__module__': '451c9ffbc8ac2f0a79be23f0dbecfb043cb840343b18e1fde03ec8feb3fab0fc'},
 'pipelines/strategy_one/strategy_thirty_one_configuration.py': {'__module__': 'c17761f3c1bf325c59735b02ded012194a378946a3a50d984044cda6b1582515'},
 'src/backend/backtest_journal_memory.py': {'__module__': 'fcebb6b414584b0cc596e9e8bcedddfaa1f7fd3d1ab1b68e265f226386ab74ae'},
 'src/backend/backtest_strategy_one_configuration.py': {'__module__': '07d3131898357c5badefbd95612cb784357e5786c9194ad006bb5bfb9773d4c6'},
 'src/backend/backtest_strategy_one_coordinator.py': {'__module__': '596d97ff1207be94508c1918b108bdda83f5dd2930d7aef7c09114a11a4e1ea5'},
 'src/backend/backtest_strategy_one_execution.py': {'__module__': 'b924c30ac06bb2a12edea845377e31ad5269154296ab7207f532ad0f38b12a80'},
 'src/backend/backtest_strategy_one_management.py': {'__module__': '4eb215447f0ac551c02cf7a6f5d357be5053d71ece106b3b5548947f97f62448'},
 'src/backend/backtest_typed_projection.py': {'__module__': '05961685789902af33cc963c49287b09b759cc56d82ffbcb8050264e72629501'},
 'src/backend/backtest_typed_publisher.py': {'__module__': 'b7081eb57979fe3472a40f37c0954c4febbe6c205203941ee7177c9ca23dc32e'},
 'src/backend/replay_run_service.py': {'_confirm_profit_arming_checkpoint': 'ed974a715df0c5d8eb27b9bf5104490b608ea2c04b19c4389530c4f0b2b2a6fd',
                                       '_require_numbered_session_window': 'a9e324be41e4e9c6bced93d9f0c28e432d8da6357d6d68cedbc2d98c7fffbb77',
                                       '_save_restart_checkpoint_responsive': '2e2ae0fd1f66145caf790b865f5146cb614386c6b451ad1c00b29a5401347482'},
 'src/trading_runtime/arte_journal_commit_v4.py': {'__module__': 'fb1a530fee5999b964175c598d9c6b2d15326841134dbb5042efba736430ced3'},
 'src/trading_runtime/arte_journal_compound_v4.py': {'__module__': '4bf6f9f059a1ea2a8797842e7ab7750385df573c7e10920c0bd6c599d3592067'},
 'src/trading_runtime/arte_journal_writer.py': {'__module__': '2bfe43dad14887e5684b6e9a0c896886648aec8b3584e7506b7b2d16f3f8a26a'},
 'src/trading_runtime/arte_journal_rowbinary.py': {'__module__': '7d42c442aa6b43208c6ce6c82ca5a19e7c6e08976a930c9f38d477667b4b7e49'},
 'src/trading_runtime/arte_typed_insert_dispatch.py': {'__module__': '3b5a254f6e4a21527ad1d5379e27ee0168a176178b549658cf415be1ae4553b5'},
 'research/mlops/clickhouse.py': {'__module__': '2cf6ccee354c65b24f8ea73198a9ec1c0f033f249b3f57430a8073dcf64470c8'},
 'src/trading_runtime/arte_oms_projection.py': {'__module__': '39cfacb41bc4a95649c710440600723079719a1c9dbecd5384228ca6c9f8af2b'},
 'src/trading_runtime/arte_profit_giveback_reader_v4.py': {'__module__': '0cf0f1628422ba7c5400099068b7cfcd0e9fa0ff47f49d7b6051189480ac40a3'},
 'src/trading_runtime/arte_profit_giveback_v4.py': {'__module__': 'b7c1c45da5116512150ac7d97875b91a270d7bbf926e2c760dcae221a00999b1'},
 'src/trading_runtime/numbered_fixed_strategy.py': {'__module__': '334007da600878df78cca37e06fda2a447f34f7c56a7b71b7b912cbd275a82e4'},
 'src/trading_runtime/runtime.py': {'__module__': '6394a5a4f27196b8b8be50fc63c6734f11b03af946b3c142999c304f168b5986'},
 'src/trading_runtime/strategy_one_management_snapshot.py': {'__module__': '0b3d19cc50cc65f5cc011e3f2357adc015b73cbe32788c780c4b15b245b9aba7'},
 'src/trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'src/trading_runtime/strategy_profit_giveback_arm.py': {'__module__': '0015308b6058f3938e4b4abb6feddca251ec161e2225f6e0ed2d3fd30e60dbc3'},
 'src/trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'src/trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'ee1ea9f934f60c9414cb7766780064915a42160cdfa50db44959239b88ffed18'},
 'src/trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'eb0f873f752a930f8e43fb8a6d96b172bf0e8c6adcc5a19f2d6f007934a5a832'},
 'src/trading_runtime/strategy_registry.py': {'__module__': 'f0eeecab59f478c0643e7d443b0b37d5cf4dcba0e1a53989f417e87bc38239aa'},
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
